FROM alpine:3.20
RUN apk add --no-cache busybox-extras curl iproute2 tcpdump nftables iptables iproute2-tc coreutils sed gawk grep findutils tzdata python3 && \
    mkdir -p /www/cgi-bin /www/iptv /tmp/iptv /etc/soso-iptv-stats
COPY rootfs/ /
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /www/cgi-bin/iptv-action /www/cgi-bin/iptv-status \
    /usr/local/bin/uci /usr/local/bin/jsonfilter /usr/local/bin/ifstatus /entrypoint.sh 2>/dev/null || true
ENV PATH="/usr/local/bin:${PATH}"
EXPOSE 8080
VOLUME ["/www", "/tmp/iptv", "/etc/soso-iptv-stats"]
ENTRYPOINT ["/entrypoint.sh"]
