# Hermes Skills

Skills for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

> **Note on `description` (this repo follows its own advice).**
> Hermes renders only the **first 57 characters** of a skill's `description` into the
> system-prompt skill index — anything past that is silently cut and never routes.
> So every description here front-loads the phrases a user would actually *say*.
> The `triggers:` field is **not** read by the Hermes core; it is only used by local
> tooling. These skills therefore omit it. See `skill-writing-convention` below.

## Skills

### neat-freak
Post-session cleanup and sync — check what changed, align wiki/memory/skills/docs.
Trigger: `/neat`, `整理一下`

### triple-lens
Deep structural analysis through three converging lenses.
Trigger: `分析一下`, `本质是什么`

### workspace-status-audit
Read-only workspace audit: three-way disk breakdown (real data / `.git` / caches),
staleness detection against the user's actual state, and delta-export packaging.
Ships the measurement scripts and the hard-won pitfalls (see below).
Trigger: `检视工作区`, `磁盘占用`, `哪些过时了`, `备份检查`

### hermes-docker-maintenance
Upgrading and maintaining a Docker-deployed Hermes: version checks, config editing,
disk, memory pressure. Includes the config-schema migration trap.
Trigger: `Hermes 升级`, `改config`, `重启gateway`, `清镜像`

### hermes-cron-governance
Auditing and fixing Hermes cron jobs: silent delivery failures, push overload,
watchdog design, timezone traps.
Trigger: `列一下cron`, `暂停cron`, `cron 很久没收到`, `投递失败`

### skill-writing-convention
How to write a SKILL.md that actually routes: the 57-character description window,
the three-layer prompt cache, and the YAML frontmatter traps that fail *silently*.
Trigger: `新建skill`, `修改skill`, `skill 写作规范`

### claim-evidence-audit
Separate how strongly a claim is *asserted* from how strongly it is *supported*.
Trigger: `这报告可信吗`, `站得住脚吗`, `证据够不够`, `帮我挑刺`

### structured-document-extraction
Turn a long document into a structured dataset: measure first, batch by output size,
append JSONL incrementally, extract metrics programmatically, cross-check.
Trigger: `全量分析`, `结构化提取`, `做成数据集`, `报告太长了`

## Install

```bash
hermes skills install XIAHONGYUU/hermes-skills --skill <name>
```

## Support files

Some skills ship `scripts/` and `references/`. Only publicly shareable material is
included — dated audit snapshots and case archives that contain instance-specific
data are **not** published. Where a skill's body mentions such a file, treat the
mention as a pointer to that author's local copy.

## License

MIT
