# 安装说明

本项目用于已经开通 **本地运营商 IPTV** 的家庭网络。它负责把运营商 IPTV 专网/组播业务整理成本地可播放的节目单和 `udpxy` 单播地址，不提供公共 M3U 频道资源。

## 支持环境

推荐：

- OpenWrt / ImmortalWrt / HigoOS 或兼容固件。
- `uhttpd`、`curl`、`udpxy`、`tcpdump`、`tc`、`python3`。
- 至少一个 LAN 网口和一个 IPTV 专用网口。

可选：

- Docker，适合管理页和 EPG/台标工具场景。
- php-epg / NAS，可作为一次性台标素材源。

## OpenWrt 手动包

手动包适合 HigoOS 或 `opkg` 不兼容自制 IPK 的环境。

```sh
scp release/soso-iptv-gateway_1.1.5_manual.tar.gz root@路由器IP:/tmp/
ssh root@路由器IP
cd /tmp
tar -xzf soso-iptv-gateway_1.1.5_manual.tar.gz
sh install.sh
```

安装后访问：

```text
http://路由器IP:8080/iptv/
```

## IPK

```sh
scp release/soso-iptv-gateway_1.1.5-1_all.ipk root@路由器IP:/tmp/
ssh root@路由器IP
opkg install /tmp/soso-iptv-gateway_1.1.5-1_all.ipk
```

如果提示 `Malformed package file`，请使用手动包。

## Docker

```sh
cd docker
docker compose up -d --build
```

Docker 版默认使用 host 网络和 privileged，便于抓包和网络诊断。它不保证能替代 OpenWrt 侧的真实网络配置。

## 首次配置流程

1. 打开 `http://路由器IP:8080/iptv/`。
2. 用默认账号 `admin / admin` 登录并修改密码。
3. 选择 IPTV 输入口和盒子抓包口。
4. 点击“新装一键配置(到可播放)”。
5. 检查健康栏：
   - IPTV 桥 UP
   - IPTV 地址已获取
   - 代理运行中
   - 组播直连
   - 频道数量正常
   - EPG 正常
6. 播放器使用：

```text
http://路由器IP:8080/new-logo.m3u
```

7. EPG 使用：

```text
http://路由器IP:8080/epg.xml.gz
```

## 卸载

IPK 安装可用：

```sh
opkg remove soso-iptv-gateway
```

手动包安装时，可参考 `openwrt/control/prerm` 删除相关文件。正式操作前请备份 `/www/new.m3u`、`/www/new-logo.m3u` 和 `/www/iptv/logos/`。
