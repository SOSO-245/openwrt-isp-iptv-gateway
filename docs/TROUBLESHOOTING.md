# 常见问题

## HigoOS 提示 IPK malformed

部分 HigoOS 的 `opkg` 会拒绝自制 IPK 并提示：

```text
Malformed package file
```

请改用手动包：

```sh
tar -xzf soso-iptv-gateway_1.1.5_manual.tar.gz
sh install.sh
```

## 页面能打开，但播放器黑屏

优先检查：

- `udpxy` 是否运行。
- `udpxy` source 是否等于当前 IPTV DHCP 地址。
- 默认路由是否走 LAN 网关。
- `239.0.0.0/8` 是否走 IPTV 接口。
- Nikki / OpenClash 是否代理了组播。

## EPG 能更新，但没有台标

XMLTV 源必须包含类似内容才可提取台标：

```xml
<icon src="https://example.com/logo.png" />
```

只有节目数据、没有 `icon src` 的 EPG 源不能提供台标。

## NAS/php-epg 还需要吗

不需要作为长期依赖。它可以作为一次性台标素材库，把已有图标同步到旁路由本地 `/www/iptv/logos/` 后，播放器和 Emby 只读旁路由本机。

## LuCI 服务页报错

如果 LuCI 报 `left-hand side is not a function` 或 `Unable to establish ubus connection`：

1. 先确认 `ubus list` 是否正常。
2. 清理 `/tmp/luci-indexcache.*` 和 `/tmp/luci-modulecache`。
3. 重启 `rpcd`，重载 `uhttpd`。
4. 不要优先 `killall ubusd` 手动拉服务，某些 HigoOS 认证后端会因此失效。
