# OpenClaw 避坑

## PIT-010：OpenClaw 返回 401 / 认证 token 不匹配

**现象：** API 请求返回 401，或 scheduler 日志显示 `authentication failed`。

**根因：** `openclaw.json` 中的 `gateway.auth.token` 与 K8s Secret / values 中的 `openclawToken` 不一致。

**排查步骤：**
```bash
# 查看运行时 token 配置
python3 -c "import json; d=json.load(open('/home/node/.openclaw/openclaw.json')); print(d['gateway']['auth'])"

# 查看 K8s Secret 中的 token
sudo k3s kubectl get secret hci-platform-secrets -n hci-troubleshoot -o jsonpath='{.data.openclaw-token}' | base64 -d
```

两者必须完全一致。修改后需 `kubectl rollout restart deployment/openclaw`。

---

## PIT-013：OpenClaw 启动报 JSON Parse Error / non-loopback 绑定失败

**现象：** Pod 持续 CrashLoopBackOff，日志出现：
- `JSON parse error at line 110` 或 `unexpected end of file`
- `Gateway: will not start on non-loopback interface`

**根因：** `/home/node/.openclaw/openclaw.json` 文件被截断（缺少末尾 `}`），配置解析失败，`dangerouslyAllowHostHeaderOriginFallback: true` 未被读取，导致 Gateway 拒绝绑定非 loopback 地址。

**修复：**
```bash
# 检查文件末尾
tail -5 /home/node/.openclaw/openclaw.json

# 如果最后一行不是 }，补上
echo "}" >> /home/node/.openclaw/openclaw.json

# 验证 JSON 完整性
python3 -c "import json; json.load(open('/home/node/.openclaw/openclaw.json'))" && echo "JSON OK"

# 重启 Pod
sudo k3s kubectl rollout restart deployment/openclaw -n hci-troubleshoot
```

**注意：** 该文件是宿主机 HostPath 挂载进 Pod，修改宿主机文件后 Pod 重启即生效，**无需重建镜像**。

---

## PIT-026：OpenClaw Control UI 报"requires device identity (use HTTPS or localhost)"

**现象：** 通过 HTTP 外网 IP 访问 `/openclaw/` 时，WebUI 报错：
> `Control UI requires device identity (use HTTPS or localhost secure context)`

**根因：** `openclaw.json` 的 `gateway.controlUi` 中缺少 `dangerouslyDisableDeviceAuth: true`。
浏览器在非 HTTPS / 非 localhost 环境下无法完成设备身份标识，OpenClaw 默认拒绝访问。

**修复：**
```bash
# 宿主机直接修改（K3s Pod 使用 hostPath /home/node 挂载，无需进容器）
python3 -c "
import json
path = '/home/node/.openclaw/openclaw.json'
with open(path) as f:
    cfg = json.load(f)
cfg['gateway']['controlUi']['dangerouslyDisableDeviceAuth'] = True
with open(path, 'w') as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
print('Done')
"

# 重启 Pod 使配置生效
k3s kubectl rollout restart deployment/openclaw -n hci-troubleshoot
k3s kubectl rollout status deployment/openclaw -n hci-troubleshoot
```

**正确的 controlUi 配置：**
```json
"controlUi": {
  "enabled": true,
  "dangerouslyAllowHostHeaderOriginFallback": true,
  "dangerouslyDisableDeviceAuth": true
}
```

**注意：** Docker farm 实例的 `openclaw.json` 由 `/srv/openclaw/data/instance-N/.openclaw/openclaw.json` 管理，
K3s 实例由宿主机 `/home/node/.openclaw/openclaw.json` 管理，两者独立，初始化时都需要设置。

---

## PIT-027：OpenClaw 聊天报 LLM request timed out（Clash TUN 劫持 API 域名）

**现象：** 在 OpenClaw Control UI 发送消息后报错：
> `LLM request timed out.`

Pod 日志出现：
```
[agent/embedded] embedded run agent end: isError=true error=LLM request timed out.
```

**根因：** `openclaw.json` 配置的模型 provider 为 `zai`，OpenClaw 默认访问 `api.zai.chat`。
该域名被 Clash TUN 劫持解析到 `198.18.0.4`，TLS 握手失败（`SSL_ERROR_SYSCALL`），请求超时。

**排查步骤：**
```bash
# 1. 查看当前使用的模型
python3 -c "import json; c=json.load(open('/home/node/.openclaw/openclaw.json')); print(c['agents']['defaults']['model'])"

# 2. 测试 API 域名连通性
curl -v --max-time 10 https://api.zai.chat/v1/models 2>&1 | tail -5
# 若解析到 198.18.x.x 且 SSL 握手失败 → Clash TUN 劫持，参见 PIT-014

# 3. 测试备用 provider 连通性
curl -o /dev/null -w "HTTP:%{http_code} time:%{time_total}s\n" --max-time 10 \
  https://open.bigmodel.cn/api/paas/v4/models \
  -H "Authorization: Bearer <your-api-key>"
```

**修复方案 A（推荐）：切换到可达的 provider**
```bash
python3 -c "
import json
path = '/home/node/.openclaw/openclaw.json'
with open(path) as f: cfg = json.load(f)
cfg['agents']['defaults']['model'] = {'primary': 'tly/glm-5'}
cfg['agents']['defaults']['models'] = {'tly/glm-5': {'alias': 'GLM-5'}}
with open(path, 'w') as f: json.dump(cfg, f, indent=2, ensure_ascii=False)
print('Done')
"
k3s kubectl rollout restart deployment/openclaw -n hci-troubleshoot
```

**修复方案 B：为 zai provider 显式配置可达的 baseUrl**
在 `openclaw.json` 的 `models.providers.zai` 中加入：
```json
"zai": {
  "baseUrl": "https://直连可用的中转地址/v1",
  "apiKey": "你的-zai-api-key"
}
```

**预防：** 初始化 openclaw.json 时优先使用 `open.bigmodel.cn` 等国内可直连域名的 provider；
避免使用 Clash TUN 会劫持的境外域名，或提前配 NO_PROXY 排除 AI API 域名（参见 PIT-014）。
