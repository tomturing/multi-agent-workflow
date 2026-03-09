# K8s / K3s / Helm 运维避坑

## PIT-014：Clash TUN 模式劫持 K8s ClusterIP 流量

**触发场景：** 宿主机开启 Clash TUN 模式，K8s Pod 间通过 Service（ClusterIP）调用时超时或断连。

**现象：** Pod IP 直连正常，Service DNS / ClusterIP 调用返回空响应（`Remote end closed connection without response`）。api-gateway 日志出现 `Server disconnected without sending a response`。

**根因：** Clash TUN 注入 `ip rule 9002: not from all iif lo lookup 2022`，将经过 iptables DNAT 后的 ClusterIP 包重定向到 Meta 虚拟网卡，绕过正常路由。

**本机已有永久修复：**
```bash
# 验证 bypass rules 存在
ip rule list | grep "priority 100"
# 应看到：
# 100: from all to 10.42.0.0/16 lookup main
# 100: from all to 10.43.0.0/16 lookup main
# 100: from 10.42.0.0/16 lookup main
```

若 rules 丢失（如系统重置了 ip rule），执行恢复：
```bash
sudo systemctl restart k8s-routing-bypass.service
```

**预防配置：**
- `/etc/systemd/system/k8s-routing-bypass.service`（开机自启）
- `~/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/Merge.yaml`（Clash Verge TUN exclude-address）

---

## PIT-015：Helm release 卡在 pending-upgrade

**现象：** `helm list` 显示 `STATUS: pending-upgrade`，后续所有 upgrade 命令报错。

**根因：** `helm upgrade --wait` 超时（如 Pod 未就绪），release 被标记为 pending-upgrade 而非 failed。

**修复：**
```bash
# 查看历史，找最后一个 deployed 的 revision
helm history hci-platform -n hci-troubleshoot

# 回滚清除锁
helm rollback hci-platform <revision号> -n hci-troubleshoot

# 再次部署时不带 --wait（或加足够长的 timeout）
helm upgrade --install hci-platform ./deploy/helm/hci-platform \
  --namespace hci-troubleshoot \
  -f ./deploy/helm/hci-platform/values.yaml \
  -f ./deploy/helm/hci-platform/values-prod.yaml \
  -f ./.local/values-prod.override.yaml \
  --timeout 15m
```

**注意：** 本项目 `k3s-deploy-prod.sh` 默认带 `--wait`，在 Pod 未完全就绪时会触发此问题。

---

## PIT-016：K3s 镜像必须手动导入，不读取 Docker daemon

**现象：** Docker 镜像构建成功，`docker images` 可见，但 Pod 一直 `ImagePullBackOff` 或拉取旧镜像。

**根因：** K3s 使用独立的 containerd 实例（不是 Docker daemon），两者镜像存储完全隔离。

**修复：**
```bash
# 每次构建后必须导入
docker save <image>:<tag> | sudo k3s ctr images import -

# 或使用项目脚本（已集成 build+save+import）
IMAGE_TAG=<tag> bash scripts/k3s-build.sh

# 验证已导入
sudo k3s ctr images list | grep hci
```

---

## PIT-017：scheduler-service 重启次数虚高（RESTARTS 累计不清零）

**现象：** `kubectl get pods` 看到 scheduler-service `RESTARTS > 10`，误以为服务异常。

**根因：** K8s 的 RESTARTS 是累计值，不会清零。之前 OpenClaw 崩溃期间 scheduler 反复重试积累的历史次数。

**判断方式：**
```bash
# 看 AGE 和最后一次重启时间，而不是重启次数
sudo k3s kubectl get pods -n hci-troubleshoot
sudo k3s kubectl describe pod <scheduler-pod> -n hci-troubleshoot | grep "Last State\|Started\|Finished"
```
当前状态 `1/1 Running` 且上次重启时间超过 10 分钟即为正常。

---

## PIT-018：HostPath 挂载文件被截断（openclaw.json 等宿主机配置文件）

**现象：** Pod 启动时日志出现 `JSON parse error` / `unexpected end of file`，但宿主机文件看起来存在。

**根因：** 宿主机上的配置文件（如 `/home/node/.openclaw/openclaw.json`）在编辑过程中被截断，缺少末尾结构（如 `}`），导致容器内解析失败。

**排查：**
```bash
# 验证 JSON 完整性
python3 -c "import json; json.load(open('/home/node/.openclaw/openclaw.json'))" && echo "OK"

# 查看文件末尾
tail -5 /home/node/.openclaw/openclaw.json
```

**修复：**
```bash
# 如末尾缺 }
echo "}" >> /home/node/.openclaw/openclaw.json
python3 -c "import json; json.load(open('/home/node/.openclaw/openclaw.json'))" && echo "OK"
sudo k3s kubectl rollout restart deployment/openclaw -n hci-troubleshoot
```

**后续改进方向：** 将 openclaw.json 纳入 ConfigMap 管理，避免依赖手动维护的宿主机文件。

---

## PIT-019：HostPath 挂载 Pod 因 UID 不匹配无法读写宿主机目录

**现象：** Pod 日志出现 `permission denied` 访问挂载目录，或容器内写文件失败。

**根因：** Helm Chart 的 `securityContext.runAsUser` 与宿主机目录 owner UID 不一致。Ubuntu 默认第一个用户 UID=**1000**，但代码里写的是 1001。

**排查：**
```bash
# 确认宿主机用户 UID
id <username>
# 确认宿主机目录 owner
ls -lan /home/node/.openclaw/
# 确认 chart 中的 runAsUser
grep -r "runAsUser" deploy/helm/
```

**修复：** 将 `openclaw-service.yaml` 中 `runAsUser/runAsGroup/fsGroup` 改为与宿主机 node 用户一致（当前机器为 `1000`）。已在项目代码中修正。

## PIT-021：K3s Traefik 宿主机端口修改方法（避开 80/443 高危端口）

**场景：** 生产环境需将 Traefik 对外端口从 80/443 改为非特权端口（如 4888/4443），避开高危端口扫描限制或 NAT 规则限制。

**错误做法：** 直接 `kubectl patch svc traefik` 修改 port，升级 K3s 或 Traefik 时会被覆盖还原。

**正确做法：** 创建 `HelmChartConfig` 覆盖 Traefik Helm values，K3s 会持久保留：

```bash
cat << 'MANIFEST' | sudo tee /var/lib/rancher/k3s/server/manifests/traefik-custom.yaml
apiVersion: helm.cattle.io/v1
kind: HelmChartConfig
metadata:
  name: traefik
  namespace: kube-system
spec:
  valuesContent: |-
    ports:
      web:
        exposedPort: 4888   # 宿主机对外端口
        port: 8000          # Traefik 内部端口（不变）
      websecure:
        exposedPort: 4443
        port: 8443
MANIFEST
```

K3s 约 10-30s 后自动 reconcile，无需重启。

**注意：**
- Ingress 注解 `traefik.ingress.kubernetes.io/router.entrypoints: web` 使用的是**内部 entrypoint 名称**，不是端口号，无需修改
- NAT/防火墙层需将 Hypervisor 端口映射目标改为 4888（原 80）
- Traefik Pod 内部端口（8000/8443）不受影响，集群内部访问无变化

## PIT-022：Helm DATABASE_URL 密码含特殊字符（@ # : 等）导致连接失败

**现象：** case-service / conversation-service / scheduler-service 启动后 API 返回 500，日志报 `password authentication failed` 或 `socket.gaierror: Name or service not known`。

**根因：** DATABASE_URL 通过 K8s env var 拼接：
```yaml
value: "postgresql+asyncpg://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@postgres:5432/..."
```
密码含 `@`（如 `aihci@aclient2025`）→ URL 解析器以最后一个 `@` 为主机分隔符 → 用户名/密码被截断错误，认证失败。

**修复：** 改为在 Helm 模板渲染时用 `urlquery` 编码密码：
```yaml
value: {{ printf "postgresql+asyncpg://%s:%s@postgres:5432/%s"
    .Values.config.postgresUser
    (.Values.secrets.postgresPassword | urlquery)
    .Values.config.postgresDb | quote }}
```
`aihci@aclient2025` → `aihci%40aclient2025`，asyncpg/SQLAlchemy 会正确解码。

**规则：** 数据库密码、Redis 密码**禁止含** `@ : / # ? =` 等 URL 特殊字符，或必须在 Helm 模板中用 `urlquery` 编码后再拼 URL。

---

## PIT-023：Docker 容器端口映射外网访问 ERR_EMPTY_RESPONSE（Clash TUN 劫持 172.16/12）

**现象：** telnet 端口通、宿主机本地 curl 200、外网浏览器 `ERR_EMPTY_RESPONSE`。

**根因：** k3s + Clash TUN 共存时，`k8s-routing-bypass.service` 只为 k3s 的 `10.42/10.43` 添加了 bypass 规则，Docker 网段 `172.16.0.0/12`（含 `172.17/18/19...`）未加 bypass。外部流量经 iptables DNAT 转到 `172.18.x.x` 后，被 Clash rule 9002 劫持进 Meta TUN，无法到达容器，服务端直接 RST。

**修复：** 在 `/etc/systemd/system/k8s-routing-bypass.service` 的 ExecStart/ExecStop 中追加：
```
ip rule add priority 100 to 172.16.0.0/12 lookup main 2>/dev/null || true
ip rule add priority 100 from 172.16.0.0/12 lookup main 2>/dev/null || true
```
然后 `sudo systemctl daemon-reload && sudo systemctl restart k8s-routing-bypass`。

**验证：** `ip rule list | grep 172.16` 应看到两条 priority 100 规则。

---

## PIT-024：Traefik Ingress 无法跨命名空间引用 Service

**现象：** Ingress 中指定的 Service 名称在当前命名空间找不到（`Cannot create service: not found`），流量回退到优先级更低的路由规则，表现为访问 `/grafana` 等子路径返回其他服务（如 customer-ui）的内容。ExternalName Service 虽能创建但 Traefik 同样拒绝（`externalName services not allowed`）。

**根因：** Traefik Kubernetes Ingress Provider 要求 Ingress 资源和其引用的 Service 在**同一命名空间**。Ingress 在 `hci-troubleshoot`，但 grafana Service 在 `hci-observability`，Traefik 无法解析，整条路由规则被丢弃。

**正确方案：** 利用 Traefik 会扫描**全集群所有命名空间** Ingress 的特性，直接在 Service 所在命名空间（`hci-observability`）创建 Ingress，路由 `/grafana` → `grafana:3000`：
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: grafana-ingress
  namespace: hci-observability   # 和 grafana Service 同一命名空间
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: web
spec:
  rules:
    - http:
        paths:
          - path: /grafana
            pathType: Prefix
            backend:
              service:
                name: grafana
                port:
                  number: 3000
```

**错误方案（不可用）：**
- ExternalName Service 桥接：Traefik 明确禁止（`externalName services not allowed`）
- 在 hci-troubleshoot 命名空间创建同名 ClusterIP：需手动维护 Endpoints IP，动态不稳定

**诊断命令：**
```bash
# 1. 看 Traefik 有无 Cannot create service 报错
k3s kubectl logs -n kube-system -l app.kubernetes.io/name=traefik --tail=50 | grep -E "ERR|grafana"

# 2. 验证 Service 是否在 Ingress 同一命名空间
k3s kubectl get svc grafana -n <ingress所在namespace>
```

## PIT-028：Clash TUN 宿主机上 Docker build 容器无法访问网络（npm install / apt-get 超时）

**现象：** `docker build` 时 `RUN npm install` / `RUN apt-get install` 报：
```
ETIMEDOUT 198.18.x.x:443
```
即使配了国内 mirror（npmmirror.com / mirrors.ustc.edu.cn）也同样超时。

**根因：** Docker 构建容器默认使用独立 bridge 网络，不在 Clash TUN 管理范围内；
Clash TUN 会把 DNS 解析劫持到 `198.18.x.x`（虚拟 IP），容器直接访问该 IP 没有对应
出口，连接无法建立。

**修复：** 构建时加 `--network host`，让容器复用宿主机完整网络栈（走 Clash 代理）：
```bash
docker build --network host -t <image>:<tag> -f <Dockerfile> <context>
```

**参见：** `frontend.md` PIT-028（npm 场景详解）；`network-service-check.md` §二（Clash TUN 全面诊断）
