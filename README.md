# SteamZG 单机游戏更新监控

这个项目会每 30 分钟检查一次：

https://steamzg.com/category/单机游戏/

发现新的游戏文章后，会通过 Telegram 发送通知，通知里包含游戏标题和链接。脚本会自动翻页检查，不只看第一页。已经推送过的文章会保存在 `data/seen_games.json`，避免重复推送。

## 目录结构

```text
steamzg-game-monitor/
├── .github/
│   └── workflows/
│       └── monitor.yml
├── data/
│   └── seen_games.json
├── src/
│   └── monitor.py
├── .gitignore
├── README.md
└── requirements.txt
```

## 文件作用

| 文件 | 作用 |
| --- | --- |
| `src/monitor.py` | 主程序：抓取网页、自动翻页、检测新文章、发送 Telegram、更新历史记录 |
| `data/seen_games.json` | 历史记录：保存已经见过的文章链接 |
| `.github/workflows/monitor.yml` | GitHub Actions 配置：每 30 分钟自动运行 |
| `requirements.txt` | 依赖说明：本项目不需要第三方依赖 |
| `.gitignore` | 忽略本地临时文件 |

## 第一次运行说明

第一次运行时，程序会把当前分类页上的文章保存进 `data/seen_games.json`，但默认不会推送 Telegram。

这样做是为了避免你刚部署时收到一大堆旧文章通知。

从第二次运行开始，如果页面出现新的文章，就会推送 Telegram。

如果你想第一次运行也推送，把 `.github/workflows/monitor.yml` 里的：

```yaml
FIRST_RUN_NOTIFY: "false"
```

改成：

```yaml
FIRST_RUN_NOTIFY: "true"
```

## 准备 Telegram Bot

1. 打开 Telegram，搜索 `@BotFather`
2. 发送 `/newbot`
3. 按提示给机器人起名字
4. BotFather 会给你一个 Token，形如：

```text
123456789:ABCxxxxxxxxxxxxxxxxxxxxxxxx
```

5. 给你的机器人发一条消息，例如：`hello`
6. 获取你的 Chat ID

可以用下面这个地址，把 `<你的BOT_TOKEN>` 替换成真实 Token 后，在浏览器打开：

```text
https://api.telegram.org/bot<你的BOT_TOKEN>/getUpdates
```

返回内容里找到类似：

```json
"chat":{"id":123456789
```

这里的 `123456789` 就是你的 `TELEGRAM_CHAT_ID`。

如果你要发到 Telegram 群，先把机器人拉进群，并给机器人发言权限，然后同样用 `getUpdates` 找群的 `chat.id`。群的 ID 通常是负数。

## 上传到 GitHub

### 方法一：用网页上传，适合新手

1. 打开 https://github.com
2. 登录账号
3. 点击右上角 `+`
4. 选择 `New repository`
5. Repository name 填：

```text
steamzg-game-monitor
```

6. 选择 `Private` 或 `Public` 都可以
7. 点击 `Create repository`
8. 进入新仓库后，点击 `uploading an existing file`
9. 把本项目里的所有文件和文件夹上传上去
10. 点击 `Commit changes`

注意：一定要把 `.github/workflows/monitor.yml` 也上传上去，否则自动运行不会生效。

### 方法二：用命令上传

把下面命令里的 `你的用户名` 改成你的 GitHub 用户名：

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/你的用户名/steamzg-game-monitor.git
git push -u origin main
```

## 设置 GitHub Secrets

上传完成后，进入你的 GitHub 仓库：

1. 点击 `Settings`
2. 点击左侧 `Secrets and variables`
3. 点击 `Actions`
4. 点击 `New repository secret`
5. 添加第一个 Secret：

```text
Name: TELEGRAM_BOT_TOKEN
Secret: 你的 Telegram Bot Token
```

6. 再添加第二个 Secret：

```text
Name: TELEGRAM_CHAT_ID
Secret: 你的 Telegram Chat ID
```

## 打开 GitHub Actions

1. 进入仓库的 `Actions` 页面
2. 如果 GitHub 提示需要启用 Actions，点击启用
3. 找到 `SteamZG Game Monitor`
4. 点击 `Run workflow`
5. 第一次运行成功后，会自动更新 `data/seen_games.json`

之后 GitHub 会按照配置每 30 分钟运行一次。GitHub 免费计划的定时任务有时会延迟几分钟，这是正常现象。

## 现在的监控方式

现在脚本不是只检查第一页，而是会：

1. 先检查第 1 页
2. 再继续检查第 2、3、4 页
3. 一直检查到遇见“上一次监控时最前面的那篇文章”为止
4. 如果还没遇到，就继续翻页，最多翻到 `MAX_PAGES_TO_SCAN`

这样即使 30 分钟内更新很多游戏，翻到后面的页面，也能继续抓到。

Telegram 提醒会先发一条汇总：

```text
距离上一次监控，共发现 X 个新增游戏。
本次共检查了 Y 页，接下来会逐条发送。
```

然后再把每个新游戏逐条发出来。

## 怎么确认它正常工作

进入 GitHub 仓库的 `Actions` 页面：

- 绿色对勾：运行成功
- 红色叉号：运行失败

点击某一次运行记录，可以看到详细日志。

如果第一次运行成功但没有 Telegram 通知，这是正常的，因为默认第一次只建立历史记录。

## 常见问题

### 为什么没有收到 Telegram？

请检查：

1. `TELEGRAM_BOT_TOKEN` 有没有填错
2. `TELEGRAM_CHAT_ID` 有没有填错
3. 你有没有先给机器人发过消息
4. 如果发到群里，机器人是否已经加入群并有发言权限

### 为什么 GitHub Actions 没有准点运行？

GitHub 免费定时任务不是精确闹钟，可能会延迟几分钟。配置是每 30 分钟触发一次，但实际执行时间由 GitHub 排队决定。

### 会不会重复推送？

不会。程序会把已经见过的文章链接写入 `data/seen_games.json`，下次检查时会跳过这些链接。

### 历史记录会无限变大吗？

`MAX_SEEN_ITEMS` 指的是：`data/seen_games.json` 最多保留多少条“最近见过的文章记录”。

它不是“最多只能提醒 500 个游戏”，也不是“只能运行 500 次”。

现在默认是 `2000`，可以在 `.github/workflows/monitor.yml` 里修改：

```yaml
MAX_SEEN_ITEMS: "2000"
```

如果你想更稳一些，可以改成：

```yaml
MAX_SEEN_ITEMS: "5000"
```

如果你真的想不设上限，也可以写成：

```yaml
MAX_SEEN_ITEMS: "0"
```

这表示不裁剪历史记录，但 `data/seen_games.json` 会越来越大，所以更推荐 `2000` 或 `5000` 这种做法。

另外还有一个参数：

```yaml
MAX_PAGES_TO_SCAN: "20"
```

这个参数表示：每次监控最多往后翻多少页。

如果你担心网站在半小时内会突然更新特别多，可以把它加大，例如：

```yaml
MAX_PAGES_TO_SCAN: "30"
```
