# OpenWrt ISP IPTV Gateway

[English](README.en.md) | 简体中文

面向 **本地运营商已开通 IPTV 业务** 的 OpenWrt / HigoOS / ImmortalWrt 旁路由 IPTV 网关管理工具。

它把运营商 IPTV 专网、组播转单播、`udpxy`、节目单、台标库、EPG、本地播放统计、抓包分析和常见网络修复集中到一个 Web 管理页面里，目标是让已经开通 IPTV 的家庭宽带从 0 配置到播放器可直接播放。

> 本项目不提供、不内置、不售卖公共 M3U 直播源或频道资源。它只用于管理你自己宽带下由本地运营商开通的 IPTV 组播/专网服务。

## 关键词

OpenWrt IPTV、HigoOS IPTV、ImmortalWrt IPTV、运营商 IPTV、IPTV 网关、旁路由 IPTV、IPTV 组播、udpxy、组播转单播、Emby IPTV、EPG、台标库、IPTV 抓包、IPTV 默认路由修复。

## 功能

- 防止 IPTV DHCP 抢默认路由，保持普通上网走 LAN 网关。
- 固定 IPTV 组播路由，避免 224/239 组播被透明代理接管。
- 自动维护 `udpxy` 源地址，IPTV DHCP 地址变化后可一键更新。
- 管理 `new.m3u` / `new-logo.m3u`，支持在线编辑、历史版本和台标重建。
- 建立旁路由本地台标库 `/www/iptv/logos/`，Emby、播放器只读旁路由本机图标。
- 汇总公网 XMLTV/EPG，生成本地 `/www/epg.xml.gz` 和 `/www/t.xml.gz`。
- 从带 `icon src` 的 EPG 源提取台标；没有图片模板时可留空。
- 可视化状态、播放统计、拓扑、抓包、VSP 候选源和自检。
- 支持 Docker 版用于管理页、节目单、台标和 EPG 工具场景。

## 适用场景

适合：

- 家里宽带已经开通运营商 IPTV，光猫有 IPTV 口或 IPTV VLAN。
- 希望保留机顶盒直通，同时让电视、手机、电脑、Emby 等本地播放器读取 `udpxy` 单播地址。
- IPTV DHCP 抢默认路由，导致旁路由普通上网、代理或插件异常，需要可视化修复。
- 想把本地节目单、台标、EPG 和播放统计统一放在旁路由上管理。

不适合：

- 想寻找公共直播源、公共 M3U、盗链频道或跨地区 IPTV 资源。
- 宽带没有开通运营商 IPTV，也没有可抓取的本地组播/专网信号。
- 只需要一个公网 m3u 播放列表管理器。

## 安装环境

### OpenWrt / HigoOS / ImmortalWrt

建议环境：

- 路由系统：OpenWrt、ImmortalWrt、HigoOS 或兼容系统。
- Web：`uhttpd` 可提供 `/www` 和 CGI。
- IPTV 播放：`udpxy`。
- 抓包/诊断：`tcpdump`、`iproute2`、`tc`、`curl`。
- EPG：`python3-light` 或可运行 `python3`。
- 推荐网络结构：一个 LAN 口接家庭网，一个 IPTV 口接光猫 IPTV 专网。

已知说明：

- 某些 HigoOS 的 `opkg` 会拒绝本项目自制 IPK 并提示 `Malformed package file`。这种环境优先使用 `release/soso-iptv-gateway_1.1.5_manual.tar.gz`。
- 安装后默认管理页地址通常是 `http://路由器IP:8080/iptv/`。
- 默认插件管理账号为 `admin / admin`，首次进入后请立即修改。

### Docker

Docker 版适合：

- 预览管理页。
- 管理节目单、台标、EPG 文件。
- 做台标/EPG 辅助工具。

Docker 版不等价于完整旁路由网络插件；桥接、默认路由、防透明代理、tc 二层透传等能力仍依赖 OpenWrt 环境。

## 快速安装

### 方式一：OpenWrt 手动包（推荐给 HigoOS）

```sh
scp release/soso-iptv-gateway_1.1.5_manual.tar.gz root@路由器IP:/tmp/
ssh root@路由器IP
cd /tmp
tar -xzf soso-iptv-gateway_1.1.5_manual.tar.gz
sh install.sh
```

然后打开：

```text
http://路由器IP:8080/iptv/
```

进入后建议执行：

1. 修改默认管理账号。
2. 点击“新装一键配置(到可播放)”。
3. 检查默认路由、IPTV 地址、组播直连、`udpxy`、频道数量和 EPG。
4. 播放器/Emby 使用 `http://路由器IP:8080/new-logo.m3u`。

### 方式二：IPK

```sh
scp release/soso-iptv-gateway_1.1.5-1_all.ipk root@路由器IP:/tmp/
ssh root@路由器IP
opkg install /tmp/soso-iptv-gateway_1.1.5-1_all.ipk
```

如果出现 `Malformed package file`，请改用手动包。

### 方式三：Docker

```sh
cd docker
docker compose up -d --build
```

访问：

```text
http://宿主机IP:8080/iptv/
```

Docker 详细说明见 [docker/README.md](docker/README.md)。

## 目录结构

```text
openwrt/      OpenWrt/HigoOS 插件 payload 和 control 文件
docker/       Docker 版运行环境
scripts/      IPK 与手动安装包生成脚本
release/      当前预构建发布包
docs/         安装、注意事项和维护说明
assets/       项目图片资源，含 15 元支付宝收款码
```

## 台标和 EPG

- `new.m3u` 是基础节目单。
- `new-logo.m3u` 是带本地台标的节目单，推荐给播放器和 Emby。
- 本地台标库位于 `/www/iptv/logos/`。
- 公网 EPG 源如果包含 `<icon src="...">`，可以同步进本地台标库。
- 如果只有 XMLTV 节目数据而没有 `icon src`，它只能提供节目表，不能提供台标。
- 可选外部 php-epg / NAS 只建议作为一次性素材库，不建议作为长期运行依赖。

## 注意事项

- IPTV 口不应承担 LAN 默认网关。
- 默认路由应走家庭主路由或 LAN 网关。
- IPTV 只保留专用组播路由，例如 `239.0.0.0/8 dev br-iptv`。
- 组播不能走 Nikki、OpenClash 或其他透明代理。
- `udpxy` 的 source 必须跟随当前 IPTV DHCP 地址。
- 使用抓包功能时请遵守当地法律法规和运营商协议。

## 开源协议

本项目使用 MIT License。详见 [LICENSE](LICENSE)。

## 免责声明

本项目仅用于家庭网络学习、个人设备管理和自有网络排障。不同地区、运营商和固件环境差异很大，使用前请确认你有权修改相关设备配置，并自行承担网络中断、配置错误或服务不可用风险。

## 打赏

如果你喜欢我的项目，或者它刚好帮你解决了 IPTV 折腾路上的一个小麻烦，可以通过下方支付宝收款码打赏 15 元请我喝杯奶茶，支持后续维护和更新。

<p>
  <img src="assets/alipay-donate-15.jpg" alt="支付宝 15 元收款码" width="360">
</p>
