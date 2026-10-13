# GitHub 长期认证 + 凭据验证（2026-09-12 修正）

> **本篇修正 `github-credential-persistence.md` 的两个结论**：
> (1) 「401 时从源仓库 remote 重新提取 token」= **死路**（源仓库内嵌 token 自身已失效）；
> (2) `git ls-remote` 成功**不能**作为凭据有效的证据。旧篇的「发现/持久化」步骤仍可用，但其验证与恢复路径以本篇为准。

## 一、验证凭据 —— 只能用认证端点

```sh
# ✅ 正确：需要认证
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: token $TOKEN" https://api.github.com/rate_limit
# 200 = 有效（认证后限额 5000）；401 = Bad credentials，已失效

# 具体仓库权限
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: token $TOKEN" \
  https://api.github.com/repos/<user>/<repo>
```

### ⚠️ 假阳性陷阱（2026-09-12 实测）

**`git ls-remote` 成功 ≠ token 有效。** 公开仓库无需认证即可读 —— 凭据完全失效时
`GIT_TERMINAL_PROMPT=0 git ls-remote <public-repo> HEAD` 照样返回 exit 0 和一个 SHA。
当时据此误判「凭据可用」，实际所有 API 调用都是 401。

**排查动作**：先查源仓库是否公开，再判断 token 状态。
```sh
curl -s https://api.github.com/repos/<user>/<repo> | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['full_name'],'private=',d['private'])"
```

**另一条教训**：从 `~/.config/git/credentials` 里用 sed 抓 token 容易抓错（字段错位、把用户名当成 token）。
用 git 自己的机制读更可靠：
```sh
printf 'protocol=https\nhost=github.com\n\n' | git credential fill | grep '^password=' | cut -d= -f2-
```

## 二、长期方案（一次配置，无需反复索取）

内嵌 token 是短效的 —— 任何「复用 + 持久化」都只是一次性便利，不能当长期基础设施。要持久化走：

| 方案 | 有效期 | 能创建仓库 | 谁操作 |
|------|--------|-----------|--------|
| **SSH key** | 永久（除非撤销） | ❌ 仅 push/pull | 容器生成，用户粘公钥 |
| **Classic PAT + Expiration=`No expiration`** | 永久 | ✅ 全部 | 用户生成一次 |
| Fine-grained PAT | 最长 1 年 | ✅ | 用户每年换 |

**推荐组合：SSH key 管 git 操作 + 无过期 classic PAT 管 API（建仓库/改设置）。**

### SSH key（容器侧生成，用户只做一次粘贴）

```sh
mkdir -p ~/.ssh && chmod 700 ~/.ssh
ssh-keygen -t ed25519 -C "hermes-agent@container" -f ~/.ssh/id_ed25519 -N ""
printf 'Host github.com\n  HostName github.com\n  User git\n  IdentityFile ~/.ssh/id_ed25519\n  IdentitiesOnly yes\n' > ~/.ssh/config
chmod 600 ~/.ssh/config
cat ~/.ssh/id_ed25519.pub
```

用户侧：https://github.com/settings/keys → **New SSH key** → Key type 选 **Authentication Key** → 粘贴公钥。
验证：`ssh -T git@github.com`。

### 无过期 classic PAT

https://github.com/settings/tokens → **Tokens (classic)** → Generate → **Expiration 选 `No expiration`** → 勾 `repo`。
拿到后写入全局凭据 store：

```sh
mkdir -p ~/.config/git
printf "https://x-access-token:%s@github.com\n" "$TOKEN" > ~/.config/git/credentials
chmod 600 ~/.config/git/credentials
git config --global credential.helper store
```

**告知用户 trade-off**：`No expiration` 的 classic PAT 是长期明文凭据（存本地 600 权限文件），
泄露即影响账号全部仓库。介意的话退回「SSH key + 每年一换 fine-grained PAT」。

## 三、约束

- **凭据值一律不出现在输出/memory/报告里** —— 只记路径与恢复流程。
- token 内嵌在 remote URL 里 = 仓库公开/fork 即泄露；发现后提醒用户清理 remote。
- 该用户账号：XIAHONGYUU。通用 GitHub 流程见 `github` skill 的 auth 部分。
