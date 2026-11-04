# 顽鹿接口与脚本

`wanlu.py` 负责顽鹿读取和原始 FIT 下载，坐标检查与转换见 [coordinates.md](coordinates.md)。Python 标准库即可，无依赖安装、常驻服务、Strava API 或账号配置文件。

## 输入

优先使用安全环境提供的 `ONELAP_TOKEN`，Authorization 保持原值，不擅自添加 Bearer；可附 `ONELAP_UID`。否则使用隐藏输入的账号 / 密码，或环境已有的 `ONELAP_ACCOUNT` / `ONELAP_PASSWORD`。不要在 shell 命令里临时写真实环境变量值。

同一进程只登录一次，凭证留在内存。不同命令可能需要再次输入；不自动缓存凭证。固定 HTTPS 主机，不接收任意代理或下载 URL，不跟随带凭证重定向。

## 协议

1. `POST https://www.onelap.cn/api/login`，multipart 字段 `account`、`password`（UTF-8 密码的 MD5 十六进制，平台协议要求，不是安全加密）。成功 `code` 为 0 或 200，`data` 可为对象或数组；读取 `token` 和 `userinfo.uid`，不输出 token。
2. OTM 基地址 `https://otm.onelap.cn/api/otm/ride_record`。
3. 先 `POST /capability_list`，JSON `{"uid":"…"}`。`data.sync.inserted / total` 判断是否继续，最多 3 轮。此操作把账号已有骑行导入 OTM 索引，可能改变该索引；不创建、修改或删除源骑行。读取最新记录时使用；仅查已有索引可加 `list --no-import`。
4. `POST /list`，JSON `page`、`limit:50`、`start_date`、`end_date`；读取 `data.list`、`data.pagination.has_more`，最多 10 页。
5. FIT 首选 `GET /analysis/fit_content/{recordId}`；备用同路径 `https://u.onelap.cn/api/otm/ride_record`。
6. 仍失败时请求主站 `GET /analysis/{recordId}`，读取 `data.ridingRecord.fileKey`（或 `data.fileKey`），将字符串 UTF-8 → 标准 Base64 → URL 路径转义，再请求主站 `/analysis/fit_content/{encoded}`。不把 fileKey 当任意下载 URL。

`code:-2` 为风控，HTTP 401 / 403 / 429 停止请求；不轮换主机或无限重试绕过。没有实现验证码破解或令牌自动刷新。

## 输出与文件

`list` 仅输出选择所需 ID、名称、起始时间、距离、时长，不输出原始登录响应、下载密钥或令牌。未知字段为空，不猜测时区。

```sh
python3 <skill-directory>/scripts/wanlu.py list --start 2026-01-01 --end 2026-01-02
python3 <skill-directory>/scripts/wanlu.py export --ids 12345 12346
python3 <skill-directory>/scripts/wanlu.py inspect /ABS/PATH/onelap-12345.fit
```

`export` 默认为本次创建临时目录；可用 `--output /ABS/NEW/DIRECTORY` 指定尚不存在的目录。目录 0700，FIT 和 manifest 0600，不覆盖已有目录或文件。检查 `.FIT` 签名、头部长度、文件长度、头部 CRC（若存在）和完整文件 CRC，再算 SHA-256。只检查容器完整性，不宣称校验每个 FIT 消息或运动语义。

失败批次保留已下载文件，输出 manifest 和失败 ID；风控或登录失效时不继续。文件和 manifest 含私人运动数据，不提交到 Git。`receipt` 原子更新本机 manifest，验证活动链接格式；`--uploaded-file` 同时记录实际上传副本的哈希，并核对转换审计记录。不执行上传或远端核验。

## 来源

接口流程参考 [WanSync](https://github.com/Tyan66666/WanSync/blob/0a168ea6b3d347e42ddab827a898b89b0a8de3b6/lib/services/onelap_client.dart)。独立 Python 实现，未复制 Flutter 源码。平台未承诺接口稳定，失效后检查响应和上游变更，不把历史验证当永久保证。
