# Strava 浏览器上传

## 使用已有会话

先读取运行环境浏览器工具的文档，使用用户选定浏览器 / 标签页；未指定时优先已有 Strava 登录会话。核对页面用户名，身份有歧义时澄清。同步授权不意味着授权购买订阅、注册开发者应用或改变已有活动。

进入 <https://www.strava.com/upload/select>，选择「文件 / File」。重定向登录时按正常流程进行。邮箱验证码由用户提供，不复用历史验证码，不写入脚本。

本机锁定导致原生浏览器不可用时，环境提供的独立浏览器可能可用，但会话独立，需要正常登录。没有通用绕过解锁办法。远程浏览器若不能读本地路径，使用工具明确支持的文件传输能力。

## 文件选择

使用原生文件选择事件 / 工具提供的 setFiles 或上传能力，不粘贴路径来假装已上传。不通过页面 evaluate 发起网络请求、读取隐藏应用状态或绕过工具权限。DOM 读取遵循工具限制。

在提供 `tab.playwright` 文件选择器的环境中，文档若支持以下 API，可以这样操作：

```javascript
const pendingChooser = tab.playwright.waitForEvent('filechooser', {timeoutMs: 10000});
await tab.playwright.locator('input[type="file"]').click();
const chooser = await pendingChooser;
// 先确认是否支持多个文件；单文件选择器逐条处理。
await chooser.setFiles(['/absolute/path/onelap-RECORD_ID.fit']);
```

这是能力示例，不替代当前工具文档；其他浏览器工具用自己的 API。

## 解析、保存与确认

先少量上传，核对每条解析后的日期、距离和数量。距离可能因 Strava 重算略有不同，不据此修改 FIT。出现解析错误、未知条目或空白表单时暂停，不猜测对应关系。

标题沿用原始或网站默认，不捏造。每条新活动选择用户要求的可见性；未指定时选「只有你 / Only You」，核对生效再保存，不接受页面默认公开作为授权。无法切换可见性时让用户决定，不顺手改全局隐私或已有活动。

等待处理并保存，打开最终活动页或自己的活动列表，核对时间与距离，取得链接 `https://www.strava.com/activities/数字`。检查可见性仍符合选择。选中两个文件不等于创建两条活动，逐条核实。

超时或保存后页面出错，先查已有活动，确认没创建后才考虑重试；不确定时记 pending，停止自动重传。提示重复时核对现存活动，不改轨迹时间强行重建。用户明确删除旧活动并要求重传时，才按要求重建。

## 无需开发者密钥

agent 像用户一样操作网站，由网站使用当前登录会话上传；无需 API 注册和 `client_secret`，也不是普通网站跨域读取 Cookie。

底层协议参考 [WanSync 网页客户端](https://github.com/Tyan66666/WanSync/blob/0a168ea6b3d347e42ddab827a898b89b0a8de3b6/lib/services/strava_web_client.dart)：`/upload/select` 获取 CSRF → `/upload/files` 提交 → `/upload/progress.json` 轮询。仅供诊断接口变化；本 Skill 默认操作可见网页，不要求用户导出 Cookie。上传队列 ID 与最终活动 ID 不同，不能混用。
