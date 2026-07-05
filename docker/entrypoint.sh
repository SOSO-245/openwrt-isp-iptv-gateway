#!/bin/sh
# 容器入口：可选环境变量
#   LAN_IP   播放器访问用的本机/路由 LAN IP（经 uci shim 提供给页面与节目单 URL）
#   EPG_HOST 可选，你的 php-epg 主机 IP，用于本地台标库与 EPG 检测
printf '%s' "${LAN_IP:-192.168.1.1}" > /etc/soso-iptv-lan-ip
[ -n "$EPG_HOST" ] && printf '%s' "$EPG_HOST" > /etc/soso-iptv-epg-host
mkdir -p /tmp/iptv /etc/soso-iptv-stats
exec busybox httpd -f -p 0.0.0.0:8080 -h /www
