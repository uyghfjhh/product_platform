# fbasecman 使用 jemalloc 排查内存增长操作指南

## 1. 适用场景与排查目标

当 `fbasecman` 在长稳运行过程中出现 `RSS` 持续增长（例如每天增长数十 MB）时，仅凭操作系统层面的内存监控难以区分以下两类根因：

1. **真实对象泄漏或缓存滞留**：代码中存在未释放的堆分配、引用计数未归零，或全局缓存/队列持续膨胀。
2. **glibc `ptmalloc2` 多线程碎片 / arena 滞留**：多线程高频交叉分配释放（例如 Worker 线程分配连接/缓存对象、`cron` 或监控线程释放）导致空闲内存无法归还操作系统。

通过 `LD_PRELOAD` 挂载 `jemalloc` 并开启 `prof:true`，可以在**无需修改业务代码、无需重编 `fbasecman`** 的前提下：
- 观察切换分配器后 RSS 是否仍按原速率上涨（快速排除或确认 glibc 碎片问题）；
- 对内存增长期前后导出的 `.heap` 快照做 **Diff（净增量对比）**，直接定位尚未释放对象的分配调用栈与源码行号。

---

## 2. 环境准备与前置检查

### 2.1 安装 `jemalloc`、`jeprof` 与绘图依赖

在运行 `fbasecman` 的测试机上执行：

- **CentOS / RHEL / Rocky / OpenEuler**：
  ```bash
  sudo yum install -y jemalloc jemalloc-devel graphviz
  ```
- **Ubuntu / Debian**：
  ```bash
  sudo apt-get update && sudo apt-get install -y libjemalloc2 libjemalloc-dev graphviz
  ```

### 2.2 确认库路径、工具与调试符号

1. **查找 `libjemalloc.so` 与 `jeprof`**：
   ```bash
   ldconfig -p | grep jemalloc
   which jeprof
   ```
   > 常见路径示例：RHEL/CentOS 一般为 `/usr/lib64/libjemalloc.so.2`；Ubuntu/Debian 一般为 `/usr/lib/x86_64-linux-gnu/libjemalloc.so.2` 或 `/usr/lib/aarch64-linux-gnu/libjemalloc.so.2`。下文中统一用 `<JEMALLOC_SO>` 代指实际路径。

2. **确认 `fbasecman` 二进制包含调试符号（未 `strip`）**：
   本项目 [CMakeLists.txt](../../CMakeLists.txt) 默认在 `Release` 和 `Debug` 模式下均携带 `-g` 编译选项。建议在测试机上再次确认：
   ```bash
   readelf -S /path/to/fbasecman | grep debug_info
   ```
   若有 `.debug_info` 段输出，则 `jeprof` 可以直接解析出函数名与源码行号；若已被 `strip`，请保留一份未 `strip` 的同版本二进制供本地解析 `.heap` 时使用。

3. **准备可写输出目录并验证 `jemalloc` 是否启用了 `prof`**：
   由于长稳配置默认开启 `daemonize yes`，`od_daemonize()` 会将标准输出与错误重定向到 `/dev/null`，因此必须为 `prof_prefix` 指定**绝对路径**且确保运行用户有写权限：
   ```bash
   mkdir -p /tmp/fb_jeprof
   chmod 777 /tmp/fb_jeprof

   LD_PRELOAD=<JEMALLOC_SO> \
   MALLOC_CONF="prof:true,prof_prefix:/tmp/fb_jeprof/verify,prof_final:true" \
   /bin/true

   ls -l /tmp/fb_jeprof/verify.*
   rm -f /tmp/fb_jeprof/verify.*
   ```
   > 若此处出现 `Invalid conf pair: prof:true`，说明发行版自带的 `jemalloc` 编译时未开启 `--enable-prof`，需从官方源码编译启用：
   > ```bash
   > ./configure --enable-prof && make -j
   > ```

---

## 3. 配置并启动带 Heap Profiling 的 `fbasecman`

### 3.1 `MALLOC_CONF` 推荐参数方案

根据需要选择以下两种采集模式之一：

- **方案 A：自动按累计分配量定期导出（无须人工介入、对进程零暂停，最推荐）**
  ```bash
  export LD_PRELOAD=<JEMALLOC_SO>
  export MALLOC_CONF="prof:true,lg_prof_sample:19,lg_prof_interval:30,prof_prefix:/tmp/fb_jeprof/fbasecman"
  ```
  - `prof:true`：开启堆采样。
  - `lg_prof_sample:19`：每累计分配 $2^{19} = 512\text{ KB}$ 采样一次调用栈（对性能影响极低，通常 `< 2%`；若需更高精度可调为 `18`，即 $256\text{ KB}$）。
  - `lg_prof_interval:30`：每累计分配 $2^{30} = 1\text{ GB}$ 自动 dump 一份 `.heap` 文件（可根据长稳 QPS 调整，例如 `29` 为 $512\text{ MB}$、`31` 为 $2\text{ GB}$，注意预留磁盘空间）。
  - `prof_prefix:/tmp/fb_jeprof/fbasecman`：生成的快照路径形如 `/tmp/fb_jeprof/fbasecman.<pid>.<seq>.i<n>.heap`。

- **方案 B：仅开启采样，按固定时间点手动导出（适合精确对比“10 分钟 vs 12 小时 / 24 小时”）**
  ```bash
  export LD_PRELOAD=<JEMALLOC_SO>
  export MALLOC_CONF="prof:true,lg_prof_sample:19,prof_prefix:/tmp/fb_jeprof/fbasecman"
  ```

### 3.2 重启 `fbasecman` 并开始压测

1. 停止现有压测与 `fbasecman` 进程（确认无残留进程）。
2. 在启动 `fbasecman` 的命令前注入上述环境变量：
   ```bash
   LD_PRELOAD=<JEMALLOC_SO> \
   MALLOC_CONF="prof:true,lg_prof_sample:19,lg_prof_interval:30,prof_prefix:/tmp/fb_jeprof/fbasecman" \
   /path/to/fbasecman /path/to/test.conf
   ```
   > **提示**：若使用长稳自动化脚本（如 `set_stabilize.sh`）部署启动，可在确认环境后重启 `fbasecman`（保持配置文件不变），或临时在启动脚本拉起 `$FBASECMAN_BIN $FBASECMAN_CONF` 这一行前加上 `LD_PRELOAD=... MALLOC_CONF=...`。
3. 记录真正运行的守护进程 PID（因 `daemonize yes` 会 `fork` 后退出父进程，`/tmp/fb_jeprof/` 下可能出现父进程短暂留下的空文件，分析时以实际进程 PID 为准）：
   ```bash
   cat /tmp/fbasecman/fbasecman.pid
   # 或
   pgrep -x fbasecman
   ```
4. 启动长稳压测（如 `test/7x24小时长稳测试/start.sh`）。

---

## 4. 采集基线与增长后快照

- **使用方案 A（自动导出）时**：
  让长稳运行至少数小时至 24 小时，直接按文件名序号挑出稳态初期快照（如刚进入压测 10~30 分钟后的第 1 份非空 `.heap`）和内存明显升高后的快照即可：
  ```bash
  PID=$(cat /tmp/fbasecman/fbasecman.pid)
  ls -lh /tmp/fb_jeprof/fbasecman.${PID}.*.heap
  ```

- **使用方案 B（或需要立即抓当前快照）时**：
  可通过 `gdb` 调用 `jemalloc` 导出的 `mallctl("prof.dump", ...)` 在线触发导出（返回 `0` 表示成功）：
  ```bash
  PID=$(cat /tmp/fbasecman/fbasecman.pid)

  # 1) 跑稳 10~15 分钟后抓基线快照（文件名按默认 prof_prefix 自动递增）
  gdb -batch -p "$PID" -ex 'call (int)mallctl("prof.dump", 0, 0, 0, 0)'

  # 2) 跑数小时至 24 小时、确认 RSS 明显上涨后抓对比快照
  gdb -batch -p "$PID" -ex 'call (int)mallctl("prof.dump", 0, 0, 0, 0)'
  ```
  > **注意**：`gdb -p` attach 时会短暂挂起目标进程数毫秒到数十毫秒；在极限低超时配置下建议避开高敏探测切换窗口，或在非高危时段执行。如果担心 `gdb` 影响压测定时，推荐优先使用**方案 A（`lg_prof_interval`）**。

---

## 5. 生成 Heap Profile Diff 并定位调用栈

设前期基线文件为 `base.heap`，后期增长文件为 `later.heap`（两者均属于同一个守护进程 `PID`）。

### 5.1 文本 Top 排行（最快排查）

```bash
jeprof --show_bytes --text --base=/tmp/fb_jeprof/base.heap \
  /path/to/fbasecman /tmp/fb_jeprof/later.heap | head -n 40
```

如需精确到源码行号，增加 `--lines`：
```bash
jeprof --show_bytes --lines --text --base=/tmp/fb_jeprof/base.heap \
  /path/to/fbasecman /tmp/fb_jeprof/later.heap | head -n 40
```

### 5.2 打印完整调用链文本（无图形环境推荐）

`--text` 只能看到汇总函数占比，使用 `--traces` 可以直接打印净增量最大的完整调用路径：
```bash
jeprof --show_bytes --lines --traces --base=/tmp/fb_jeprof/base.heap \
  /path/to/fbasecman /tmp/fb_jeprof/later.heap | head -n 120
```

### 5.3 生成可视化调用图（最直观）

```bash
jeprof --show_bytes --lines --pdf --base=/tmp/fb_jeprof/base.heap \
  /path/to/fbasecman /tmp/fb_jeprof/later.heap > /tmp/fb_jeprof/leak_diff.pdf

# 或导出为 SVG 以便浏览器直接查看：
jeprof --show_bytes --lines --svg --base=/tmp/fb_jeprof/base.heap \
  /path/to/fbasecman /tmp/fb_jeprof/later.heap > /tmp/fb_jeprof/leak_diff.svg
```

> **说明**：`jeprof` 默认统计 `inuse_space`（当前仍存活未释放的字节数），配合 `--base=base.heap` 后输出的是 **`later - base` 的净内存增量**，已释放的临时内存不会干扰结果。

---

## 6. 结果判读与下一步动作

结合压测期间的 `RSS` 曲线与 `jeprof --base` 输出，可按如下对照判断：

| 观察现象 | 结论定位 | 后续处理建议 |
|---|---|---|
| 切换为 `jemalloc` 后，`RSS` 不再每天上涨 ~80 MB（趋于平稳），且 `jeprof --base` 净增量很小 | 之前是 **glibc `ptmalloc2` 多线程碎片 / 跨 arena 滞留**（常与短 `server_lifetime` 频繁销毁重建连接池及跨线程释放相关） | 1. 生产/长稳推荐切换为 `jemalloc`；<br>2. 若继续使用 glibc，可测试设置 `MALLOC_ARENA_MAX=4`，或对比 `server_lifetime` 调大后的 RSS 趋势，并验证 `malloc_trim(0)` 效果。 |
| 切换为 `jemalloc` 后 `RSS` 仍持续增长，`jeprof --base` 清楚指出某调用链净增几十 MB | **代码内真实内存滞留或泄漏** | 根据 `jeprof` 给出的函数与行号直接排查对应模块：<br>• `fb_global_ps_*`：重点检查全局 PS 缓存淘汰（如 `bypass_response != NULL` 不淘汰）或引用未 `unref`；<br>• `mm_msgcache_*`：检查 0 长度消息缓存无限积压或 `machine_msg_t` 漏释放；<br>• `fb_sql_parse_*` / `fb_tx_guc_*`：检查解析队列、保存点栈/撤销日志或 AST 元数据清理；<br>• `fb_monitor_*` / `od_server_*` / `od_client_*`：检查快照对象或连接结构体销毁路径。 |
| `RSS` 持续上涨，但 `jeprof --base` 净增量接近 0 且 `jemalloc` 自身指标平稳 | 可能为非堆内存增长（如大页、内核页缓存、`mmap` 匿名映射未通过 `malloc`、线程栈泄漏等） | 检查 `/proc/$PID/smaps_rollup` 与 `/proc/$PID/maps`，以及 `SHOW CLIENTS` / 线程数 `ps -o nlwp -p $PID` 是否持续递增。 |

---

## 7. 常见注意事项与收尾

1. **控制台 `RELOAD;` 不影响挂载**：`fbasecman` 的 `RELOAD;` 在进程内重载配置，不会重启进程，因此不会丢失 `LD_PRELOAD`；但若发生进程重启或升级重启，新启动命令中需继续保留 `LD_PRELOAD` 与 `MALLOC_CONF`。
2. **避免 `jeprof` 解析失败**：分析用的 `/path/to/fbasecman` 必须与生成 `.heap` 文件的进程二进制完全一致，且跨机分析时建议连同对应二进制与库或符号文件一起打包。
3. **排查结束及时清理**：问题确认后取消 `LD_PRELOAD` / `MALLOC_CONF` 环境变量恢复默认运行，并删除 `/tmp/fb_jeprof/` 下的 `.heap` 与 `.pdf`/`.svg` 临时文件以释放磁盘空间。
