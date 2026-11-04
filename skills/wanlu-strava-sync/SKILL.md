---
name: wanlu-strava-sync
description: 将用户指定的顽鹿运动 / OneLap 骑行下载为原始 FIT，再通过已登录的 Strava 网页上传。适用于补同步、迁移骑行记录；无需 Strava 开发者应用密钥，需要本机 Python、网络和支持文件上传的浏览器操作工具。
---

# 顽鹿 → Strava

完成用户指定范围的骑行补同步，并返回可打开的 Strava 活动链接。使用顽鹿后台读取接口和 Strava 网页上传，不创建开发者应用，不收集 Strava Cookie，不部署服务端。

## 执行条件

- Python 3.9+、可连接顽鹿的网络、运行环境允许的浏览器操作工具；浏览器工具必须支持登录交互、文件选择和读取上传结果。
- 浏览器必须能读取脚本生成的本地 FIT 路径。远程浏览器需要工具支持的文件传输，不能只把另一台机器的路径传过去。
- 用户指定日期或记录，并授权同步到自己的 Strava。授权范围清楚时继续执行，不反复索要确认；范围不清楚时先读取列表再澄清选择。

缺少浏览器能力时，可以下载文件，但明确说明同步未完成。Skill 不会赋予 agent 浏览器控制、解锁设备或验证码接收能力。

## 读取和下载

读取 [references/onelap.md](references/onelap.md)，使用随包脚本，不重新猜测接口。

```sh
python3 <skill-directory>/scripts/wanlu.py list --start YYYY-MM-DD --end YYYY-MM-DD
python3 <skill-directory>/scripts/wanlu.py export --ids RECORD_ID_1 RECORD_ID_2
```

起止日期均包含当天，按顽鹿站点日期查询。将「前两天」等相对时间按用户时区转换为明确日期，检查返回记录的时间和距离，再选择 ID。列表最多 500 条，截断时缩小日期范围。

脚本默认通过隐藏终端输入读取顽鹿账号和密码，不回显、不保存；也支持安全运行环境提供的 `ONELAP_TOKEN` / `ONELAP_UID` 或 `ONELAP_ACCOUNT` / `ONELAP_PASSWORD`。没有命令行密码参数。不要把真实凭证写进 shell 命令、文件、Skill、日志或回复。自动化输入需要可隐藏输入的 PTY；不要为传凭证而关闭隐藏输入。

`export` 返回权限受限目录中的 FIT 路径和 `manifest.json`。只导出选定 ID。保留原始字节，校验失败不上传。不改时间或生成替代轨迹。

## 自动检查坐标

上传前读取 [references/coordinates.md](references/coordinates.md)，运行离线分析：

```sh
python3 <skill-directory>/scripts/fit_coordinates.py analyze /ABS/PATH/original.fit
```

AI 根据来源信息和有明确坐标系的地图参照，判断原始 FIT 为 WGS-84、GCJ-02 或无法确定；不得把 FIT 半圆整数单位误当成另一种坐标系，不能只凭「在中国」「来自顽鹿」或数值范围判定 GCJ-02。

已确认大陆 GCJ-02 时自动生成 WGS-84 副本并上传该副本，保存判断依据和原始 / 副本哈希；用户已要求自动识别纠偏，不为有充分证据的正常转换重复索要确认。确认 WGS-84 或无 GPS 时保留原文件；证据不足、疑似 BD-09、港澳台或跨境时不猜测转换，说明不确定性。用户要求必须纠偏且无法判断时，先澄清再上传。

## 网页上传

读取 [references/strava-browser.md](references/strava-browser.md)。使用环境支持的浏览器工具及其文档，不另外启动未授权的自动化通道。

打开 `https://www.strava.com/upload/select`：

1. 复用已登录会话，核对目标账号。需要登录或验证码时按正常流程处理。原生桌面锁定时可尝试工具明确支持的独立浏览器，并正常登录；不要绕过设备锁。
2. 通过工具支持的文件选择器填入选定 FIT，小批量上传，核对解析出的日期、距离和数量。
3. 用户明确指定可见性时遵循该选择；否则新上传记录选「只有你 / Only You」。如无法设置，上传前让用户明确选择；不改全局隐私设置。
4. 保存活动。只有最终活动页或活动列表出现对应记录及真实活动链接，才记为成功。上传队列编号或「已选择文件」不是成功凭证。

不从普通网页跨站抓 Cookie，不导出浏览器 Cookie 数据库，不把会话转交公共代理。网站上传无需 `client_secret`；浏览器自动化不等同于普通网站已经能一键跨站上传。

## 结果与恢复

每条记录分别报告「成功」「已存在」「待核实」「失败」。保存成功链接及源文件 SHA-256，便于核对。可将观察到的结果写入 manifest：

```sh
python3 <skill-directory>/scripts/wanlu.py receipt --manifest /ABS/PATH/manifest.json --id RECORD_ID --status success --url https://www.strava.com/activities/ACTIVITY_ID --uploaded-file /ABS/PATH/actually-uploaded.fit
```

此命令只记录结果，不查询 Strava，也不证明上传成功；agent 必须先在网页验证。`existing` 也需现存活动链接，`pending` / `failed` 无需链接。

保存后断网、超时或报错，先查 Strava 是否已有活动；不能确认时记 `pending`，不自动重传。提示重复则核对现存活动，不改时间绕过去重。登录失效、风控或频率限制时停止批次，保留剩余记录。

最终回复包含明确日期、各条结果、活动链接、上传可见性、坐标判断及是否转换，以及未完成部分。临时目录包含位置和运动数据；按用户要求可保留，全部完成后可清理本次临时文件，待核实时保留以便恢复，不清理无关文件。

## 已验证边界

2026-11-04 曾完成真实顽鹿登录、读取、下载，并在独立浏览器中将两条原始 FIT 通过 Strava 网页保存为仅自己可见，用户确认成功。本包脚本是从该流程提取后的独立实现；离线测试不等于再次实测线上成功。

坐标分析和转换已做合成 FIT 的离线验证，未用真实骑行验证 AI 判别准确率，不宣称能仅凭数字自动识别全部坐标系。未验证 iPhone 上独立运行整套 agent；不是定时后台同步，不保证未公开接口长期不变。详细来源在上述 references 中。
