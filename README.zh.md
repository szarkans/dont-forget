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

```
/plugin marketplace add szarkans/dont-forget
/plugin install dont-forget@dont-forget

or
claude plugin marketplace add szarkans/dont-forget
claude plugin install dont-forget@dont-forget
```

然后

`/dont-forget:setup`

然后

接下来照常用 Claude Code 就行。用一阵子之后，你就有了个类似第二大脑的东西。

依赖：只要 `python3`。不用 pip 装任何东西 —— 只用标准库。

## 命令

`/dont-forget:that` —— "来，把这个事实／坑／决定存下来，别忘了。"写成原子笔记；
若内容里疑似夹带密钥会提醒你，库里已有说法相近的笔记时会停下来先问。

`/dont-forget:about` —— "我们当初为啥换成 postgres 了？""这儿到底哪里不对？"
在笔记库里检索并给出带引用的回答，附一份诚实的覆盖率报告 ——
包括在库里确实没有相关内容时直接告诉你。

`/dont-forget:session` —— 会话收尾用。由新的子代理去读这次会话的转录，
报出哪些内容没能进入记忆；在你点头之前不写入任何东西。随后写入会话笔记、
关掉有凭据证明已完成的线索，并检查笔记库。

`/dont-forget:health` —— **笔记库**的健康检查：把它提交到 git，并报告索引实际看到了什么。

`/dont-forget:audit` —— 偶尔做一次的慢读：哪些笔记声明的失效条件可能已经发生、
哪些笔记总在回答同样的问题因而也许本是一条、以及笔记库反复指向却没有任何笔记回应的名字。
它只提议，由你决定。

`/dont-forget:setup` —— 把插件指向你的笔记，之后也可以改指到别的文件夹。
它自己去找笔记库，而不是让你敲路径。

## 它自己会做的事

每次会话开始时，按你所在的项目注入两份短清单：最新的未完成线索 —— 也就是会话笔记里
没打勾的复选框 —— 以及最新的坑，也就是会绊你一跤的事，而不是你要去做的事。
默认各十五条，其余的用检索去拿。不需要任何命令。

另外，快要自动压缩的时候会提醒你跑一下 `/dont-forget:session`，这样进度不会丢，
下次会话也能接得上。

## 为什么 readme 写成这样

因为是我这个人类写的。我受够了那种 b2b-ai-saas-agentic-loop-skills 式的描述，
读起来太折磨人了。

大白话，讲事实。其余细节问你的智能体去。

## 许可证

MIT
