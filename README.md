# wanlu-strava-sync

顽鹿 → Strava 的 Agent Skill。

让 agent 把指定的顽鹿运动 / OneLap 骑行同步到 Strava：读取记录，下载并校验原始 FIT，检查坐标系，再通过 Strava 网页上传并核实结果。

无需发布 App、部署同步服务或申请 Strava 开发者应用。需要 agent 具备浏览器操作和文件上传能力，可能需要你协助完成登录验证码。

## 安装与使用

下载本仓库后，将 `skills/wanlu-strava-sync` 整个目录放入 agent 支持的 skills 目录。以 Codex 为例：

```sh
mkdir -p ~/.codex/skills
cp -R skills/wanlu-strava-sync ~/.codex/skills/
```

如果该目录已经存在，先检查已有内容，按需更新，避免嵌套复制。其他 agent 按各自的 Skill 加载方式安装；也可以明确让 agent 读取 [SKILL.md](skills/wanlu-strava-sync/SKILL.md)。

对 agent 说：

> 使用 $wanlu-strava-sync，把我昨天的顽鹿骑行同步到 Strava，仅自己可见，并检查坐标是否需要纠偏。

需要：

- Python 3.9+，脚本运行仅使用标准库。
- 能连接顽鹿接口的网络、你自己的顽鹿账号。
- 可正常登录 Strava、支持文件选择和结果读取的浏览器工具。
- 浏览器能够读取下载脚本生成的 FIT，或工具支持本地文件传输到远程浏览器。

Skill 不包含浏览器自动化引擎，不会为 agent 增加权限。仅有文本对话能力的模型不能独立完成上传。

## DeepSeek Harness 安装

```sh
dsh plugin --profile web add github:bananaive/wanlu-strava-sync
```

将 `web` 换成自己的 profile。安装后在技能列表选择 `wanlu-strava-sync`，告诉 agent 要同步的日期和可见性。

插件只注册本包的 Skill 和资源，不启动同步任务、不读取账号、不上传活动；同步需要会话具备前述浏览器和文件访问能力，并获得用户授权。发行包没有安装时执行的脚本。

支持 DSH 0.2.1-rc.2 及后续 0.2.x（含 0.2.2 预发布版），已在 0.2.1-rc.2 验证注册、读取资源、卸载及临时 profile 安装；未在 Harness 中重新执行真实账号同步。

也可把 `skills/wanlu-strava-sync` 复制到 `~/.dsh/skills/`，两种安装方式任选一种。开发者安装开发依赖后运行 `npm test` 验证 Harness 集成，运行下文 Python 测试验证离线脚本。

## 工作方式

1. 按明确日期读取顽鹿记录，核对时间和距离并选择记录 ID。
2. 下载原始 FIT，检查文件长度和 CRC，生成 SHA-256。
3. AI 根据来源说明或可靠地图参照判断坐标系；确认大陆 GCJ-02 后才生成 WGS-84 副本，原始文件保留。
4. 通过已登录的 Strava 网页上传。未指定隐私时使用「只有你」，逐条核实最终活动链接。
5. 保存本地结果。上传结果不确定时先查已有活动，不自动重复提交。

源接口参考 [WanSync](https://github.com/Tyan66666/WanSync)，本项目为独立实现，未复制 Flutter 源码，不是顽鹿或 Strava 官方产品。

## 数据与凭证

账号密码仅用于当次登录，通过隐藏终端输入或安全运行环境提供；不写进命令参数、配置文件或源码。Strava 使用正常网页登录会话，不要求导出 Cookie 或填写开发者密钥。

FIT、清单和坐标审计留在执行环境的本地目录，包含敏感位置和运动数据；默认创建权限受限的目录和文件。不要把它们放进公开仓库或问题报告。下载直接连接顽鹿，上传由浏览器连接 Strava；没有项目自建的中转服务。实际 agent 平台如何处理对话及工具数据，取决于该平台。

## 工具与测试

```sh
python3 skills/wanlu-strava-sync/scripts/wanlu.py --help
python3 skills/wanlu-strava-sync/scripts/fit_coordinates.py --help
python3 -m unittest discover -s tests -v
```

```text
skills/wanlu-strava-sync/
├── SKILL.md
├── agents/openai.yaml
├── scripts/wanlu.py
├── scripts/fit_coordinates.py
└── references/
    ├── onelap.md
    ├── strava-browser.md
    └── coordinates.md
tests/
```

测试使用合成 FIT 和模拟响应，不访问真实运动账号。覆盖分页、风控中止、输出权限、FIT 校验、坐标字段转换、原文件保留及重复纠偏防护。

## 当前边界

- 完成过真实顽鹿读取和两条 Strava 网页上传，用户确认成功；抽取后的脚本通过离线测试，不代表接口永久有效。
- 顽鹿接口未公开承诺稳定，Strava 网页结构也可能改变。
- 单靠经纬度数字不能保证识别坐标系。缺少可靠证据时保持原坐标，尚未验证 AI 在真实轨迹上的识别准确率；详见 [坐标诊断说明](skills/wanlu-strava-sync/references/coordinates.md)。
- 当前仅提供有证据支持的大陆 GCJ-02 → WGS-84 近似转换，不自动处理 BD-09、港澳台、跨境或混合坐标。
- 尚未验证 iPhone 本地独立运行整套 agent，不是常驻后台自动同步服务。

## 反馈与贡献

欢迎通过 Issues / Pull Requests 反馈接口变化和浏览器适配。请提供已脱敏的错误信息、运行环境和最小复现步骤；不要上传账号、令牌、Cookie、真实 FIT、轨迹截图或含位置的清单。测试优先使用合成数据。

## 许可

[MIT](LICENSE)。使用平台服务仍需遵守各平台适用条款。
