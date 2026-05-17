# 修改镜像

本文档总结在已有镜像基础上做容器内修改，再打包为新镜像的常用流程。

## 流程概览

1. **创建旧镜像的容器**：用目标镜像起一个交互/长期运行的容器，在容器内完成修改。
2. **为旧镜像的容器进行修改**：进入容器安装依赖、改配置、打补丁等，确认行为符合预期。
3. **打包并提交为新镜像**：备份当前 `latest`（或你正在用的标签），把修改后的容器 `docker commit` 成新镜像并打回原有标签，最后删除临时容器。

---

## 1. 创建修复用容器

先删除同名容器（若存在），再用旧镜像后台运行一个带 `bash` 的容器，便于 `docker exec` 进去改：

```bash
docker rm -f evobench-openhands-fix 2>/dev/null || true
docker run -dit \
  --name evobench-openhands-fix \
  evobench-openhands:latest \
  bash
```

在容器内完成修改后，自行验证（例如 `docker exec -it evobench-openhands-fix bash`）。

---

## 2. 确认通过后：备份旧镜像并替换当前镜像

将当前 `latest` 打一个带时间戳的备份标签，再把修复容器提交为新的 `latest`：

```bash
docker tag evobench-openhands:latest evobench-openhands:broken-openhands-$(date +%Y%m%d-%H%M%S)
docker commit \
  evobench-openhands-fix \
  evobench-openhands:latest
```

说明：

- **备份标签**：便于回滚到修改前的镜像层。
- `**docker commit`**：把容器的可写层固化为新镜像；镜像名/标签可按项目替换。

---

## 3. 清理临时容器

```bash
docker rm -f evobench-openhands-fix
```

---

## 注意事项

- 修改应尽量可复现：长期维护更推荐改 `Dockerfile` 再 `docker build`，`commit` 适合应急修补或快速验证。
- Windows PowerShell 下若需等价时间戳，可自行改用 `Get-Date -Format yyyyMMdd-HHmmss` 等生成标签后缀。
- 提交前务必在容器内确认功能正常，避免把错误状态固化进新镜像。



改名

```
# 1. 给现有镜像再加一个标签（同一套层、同一个 id）
docker tag evobench-openhands:broken-openhands-20260513-140925 evobench-openhands:新名字

# 2. 只去掉旧标签（若还有别的标签指向同一 id，不会删层）
docker rmi evobench-openhands:broken-openhands-20260513-140925
```

