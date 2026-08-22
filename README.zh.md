# Don't forget

*I'm with you in the dark.*

给 AI 智能体的长期记忆 —— Zettelkasten 风格的笔记，就是普通的 Markdown 文件，Obsidian 那种。

灵感来自 [mnemo](https://github.com/jojoprison/mnemo)。

🌍 [English](README.md) · [Русский](README.ru.md)

## 这是什么

一个装满 `.md` 文件的文件夹，用来存你在项目里踩过的坑、会话交接、想法和事实 ——
凡是你（或者你的智能体）觉得值得留下的。

之后你就能想起来："哦，这个我们两年前做过！"或者"等等，就是这玩意儿两个月前
把数据库搞崩的。"搜索完全在本地跑：SQLite 全文检索，加上沿着笔记之间
`[[wiki 链接]]` 的图遍历。有时会翻出你早忘了的、来自别的项目的关联。

> 那 claude-mem 之类的记忆插件不行吗？

claude-mem 是快记忆（像内存），智能体一直看得见。dont-forget 是长期记忆（像硬盘），
你和智能体都能用。

## 安装

**1. 安装插件。** 在 Claude Code 里：

```
/plugin marketplace add szarkans/dont-forget
/plugin install dont-forget@dont-forget
```

或者在终端里：

```bash
claude plugin marketplace add szarkans/dont-forget
claude plugin install dont-forget@dont-forget
```

**2. 告诉它笔记在哪。** 这一步是必须的 —— 不做的话什么都跑不起来：

```bash
mkdir -p ~/.dont-forget
echo '{"vault": "~/你的/笔记/路径"}' > ~/.dont-forget/config.json
```

任何装着 `.md` 文件的文件夹都行。不需要 Obsidian —— 它就是个文件夹。

**3. 重启 Claude Code。** 索引会在第一次搜索时自动建立，之后自动保持更新。
没有需要你手动跑的东西。

依赖：只要 `python3`。不用 pip 装任何东西 —— 只用标准库。

> **不要用 `npx skills add`。** 那个工具只复制技能文件夹本身。本插件的技能会调用
> `scripts/` 里的辅助脚本，并注册一个会话启动钩子，这两样都不会被带过去 ——
> 装出来的技能第一次调用就会崩。这是实测结果，不是猜测。请用上面的 marketplace 方式。

## 命令

`/dont-forget:this` —— "来，把这个事实／坑／决定存下来，别忘了。"写成原子笔记，
并与库里已有的内容去重。

`/dont-forget:about` —— "我们当初为啥换成 postgres 了？""这儿到底哪里不对？"
在笔记库里检索并给出带引用的回答，附一份诚实的覆盖率报告 ——
包括在库里确实没有相关内容时直接告诉你。

`/dont-forget:session` —— 会话收尾用。把这次做了什么写进会话笔记，
并把其中未完成的线索建立索引，好让下一次会话接上。

`/dont-forget:review` —— 回看整个会话并审计：什么真做完了，什么只是宣称做完了，
哪些事实和承诺压根没进记忆。

`/dont-forget:checkup` —— **笔记库**的健康检查：把它提交到 git，并报告索引实际看到了什么。

`/dont-forget:feedback` —— 记录已证实的检索失败与命中，让搜索按证据改进，而不是凭感觉。

## 它自己会做的事

每次会话开始时，自动注入最近 7 天里未完成的线索 —— 也就是会话笔记里没打勾的复选框。
不需要任何命令。

## 为什么 readme 写成这样

因为是我这个人类写的。我受够了那种 b2b-ai-saas-agentic-loop-skills 式的描述，
读起来太折磨人了。

大白话，讲事实。其余细节问你的智能体去。

## 许可证

MIT
