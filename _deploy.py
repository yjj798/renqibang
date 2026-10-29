#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Contents API 部署器(本地 git push 走不通的替代通道)
用法: python _deploy.py data.json index.html [更多文件...]
- 凭据来自 git credential fill(gh 登录的 token), 仓库 = origin 远端
- 走本机 7890 代理 + PUT /repos/{owner}/{repo}/contents/{path}
- sha 取远端现有文件(GITHUB_TOKEN 提交不触发本仓库自身 workflow, 但会触发 Pages 重建)
"""
import base64, json, os, subprocess, sys
import requests

REPO = "yjj798/renqibang"
BRANCH = "main"
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
API = "https://api.github.com"


def token():
    inp = "protocol=https\nhost=github.com\n\n"
    out = subprocess.run(["git", "credential", "fill"], input=inp,
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise SystemExit("拿不到 GitHub 凭据(gh 未登录?)")


def main(paths):
    tok = token()
    s = requests.Session()
    s.headers.update({"Authorization": "Bearer " + tok,
                      "Accept": "application/vnd.github+json",
                      "User-Agent": "renqibang-deploy"})
    s.proxies = PROXY
    for p in paths:
        with open(p, "rb") as f:
            blob = f.read()
        body = {"message": "deploy %s" % p, "branch": BRANCH,
                "content": base64.b64encode(blob).decode()}
        r = s.get("%s/repos/%s/contents/%s?ref=%s" % (API, REPO, p, BRANCH), timeout=30)
        if r.status_code == 200:
            body["sha"] = r.json()["sha"]
        elif r.status_code != 404:
            raise SystemExit("GET %s -> %s %s" % (p, r.status_code, r.text[:200]))
        r = s.put("%s/repos/%s/contents/%s" % (API, REPO, p), json=body, timeout=60)
        if r.status_code not in (200, 201):
            raise SystemExit("PUT %s -> %s %s" % (p, r.status_code, r.text[:300]))
        print("OK", p, "->", r.json().get("commit", {}).get("sha", "")[:10])


if __name__ == "__main__":
    main(sys.argv[1:])
