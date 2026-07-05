# OpenWrt ISP IPTV Gateway

English | [简体中文](README.md)

An OpenWrt / HigoOS / ImmortalWrt IPTV gateway manager for home networks where IPTV service has already been provisioned by the local ISP.

It brings the ISP IPTV private network, multicast-to-unicast playback, `udpxy`, playlist generation, local channel logo cache, EPG generation, playback statistics, packet capture, topology diagnostics and common route/proxy repair actions into one web management page.

> This project does not provide, bundle, sell or recommend public M3U live TV sources. It is only a management tool for IPTV multicast/private-network services that are already enabled on your own broadband line by your local ISP.

## Keywords

OpenWrt IPTV, HigoOS IPTV, ImmortalWrt IPTV, ISP IPTV, IPTV gateway, home IPTV gateway, multicast IPTV, udpxy, multicast to unicast, Emby IPTV, EPG, channel logo cache, IPTV packet capture, IPTV default route repair.

## Features

- Prevent the IPTV DHCP interface from stealing the system default route.
- Keep normal internet and proxy traffic on the LAN gateway.
- Maintain dedicated IPTV multicast routes such as `239.0.0.0/8`.
- Keep multicast traffic away from transparent proxy tools such as Nikki or OpenClash.
- Update the `udpxy` source address when the IPTV DHCP address changes.
- Manage `new.m3u` and `new-logo.m3u`, including online editing, history and logo rebuilds.
- Maintain a local logo cache under `/www/iptv/logos/` so Emby and players read logos from the router.
- Generate local EPG files at `/www/epg.xml.gz` and `/www/t.xml.gz`.
- Extract channel logos from XMLTV/EPG sources that contain `<icon src="...">`.
- Provide visual status cards, network topology, playback statistics, packet capture, VSP candidates and self-check tools.
- Provide a Docker edition for UI preview, playlist editing, logo cache and EPG utility scenarios.

## Use Cases

Suitable for:

- Home broadband where the local ISP has enabled IPTV service, usually through an IPTV port or IPTV VLAN on the modem/ONT.
- Users who want to keep the ISP set-top box working while also allowing TVs, phones, computers, Emby and other local players to play IPTV through `udpxy` unicast URLs.
- OpenWrt side-router setups where the IPTV DHCP interface accidentally hijacks the default route and breaks normal internet or proxy traffic.
- Users who want local management for playlists, logos, EPG and playback statistics on the router.

Not suitable for:

- Finding public live TV sources, public M3U lists, unauthorized streams or cross-region IPTV resources.
- Networks where the ISP has not enabled IPTV service.
- Users who only need a generic public M3U playlist manager.

## Requirements

Recommended OpenWrt-side environment:

- OpenWrt, ImmortalWrt, HigoOS or a compatible firmware.
- `uhttpd` serving `/www` and CGI.
- `udpxy` for multicast-to-unicast playback.
- `tcpdump`, `iproute2`, `tc` and `curl` for diagnostics and repair actions.
- `python3-light` or a usable `python3` for EPG generation.
- One LAN interface for the home network and one IPTV interface connected to the ISP IPTV network.

Known notes:

- Some HigoOS builds reject this self-built IPK with `Malformed package file`. Use `release/soso-iptv-gateway_1.1.5_manual.tar.gz` on those systems.
- The management page is usually available at `http://ROUTER-IP:8080/iptv/` after installation.
- The default management account is `admin / admin`. Change it immediately after first login.

## Installation

### Manual OpenWrt Package

Recommended for HigoOS and systems where `opkg` rejects the custom IPK:

```sh
scp release/soso-iptv-gateway_1.1.5_manual.tar.gz root@ROUTER-IP:/tmp/
ssh root@ROUTER-IP
cd /tmp
tar -xzf soso-iptv-gateway_1.1.5_manual.tar.gz
sh install.sh
```

Then open:

```text
http://ROUTER-IP:8080/iptv/
```

Suggested first-run flow:

1. Change the default management account.
2. Click the one-click setup action for a new installation.
3. Check default route, IPTV address, multicast direct route, `udpxy`, channel count and EPG status.
4. Use `http://ROUTER-IP:8080/new-logo.m3u` in Emby or your local player.

### IPK

```sh
scp release/soso-iptv-gateway_1.1.5-1_all.ipk root@ROUTER-IP:/tmp/
ssh root@ROUTER-IP
opkg install /tmp/soso-iptv-gateway_1.1.5-1_all.ipk
```

If `opkg` reports `Malformed package file`, use the manual package instead.

### Docker

```sh
cd docker
docker compose up -d --build
```

Open:

```text
http://HOST-IP:8080/iptv/
```

The Docker edition is useful for UI preview, playlist editing, logo cache and EPG tooling. It does not replace the real OpenWrt network-side features such as bridge setup, default-route repair, transparent-proxy bypass and tc-based layer-2 passthrough.

## Directory Layout

```text
openwrt/      OpenWrt/HigoOS package payload and control files
docker/       Docker runtime
scripts/      IPK and manual package build scripts
release/      Prebuilt release packages
docs/         Installation, security and troubleshooting notes
assets/       Project assets, including the 15 CNY Alipay payment QR code
```

## Playlist, Logos and EPG

- `new.m3u` is the base playlist.
- `new-logo.m3u` is the logo-enriched playlist, recommended for Emby and local players.
- The local logo cache is stored under `/www/iptv/logos/`.
- Public XMLTV/EPG sources can provide logos only when they contain `<icon src="...">`.
- XMLTV sources without icon URLs can provide EPG data, but not channel logos.
- External php-epg or NAS-based tools are optional one-time material sources, not required runtime dependencies.

## Important Notes

- The IPTV interface should not be the LAN default gateway.
- The default route should go through the home router or LAN gateway.
- IPTV multicast should use dedicated routes, for example `239.0.0.0/8 dev br-iptv`.
- Multicast traffic should not go through Nikki, OpenClash or similar transparent proxy tools.
- The `udpxy` source must follow the current IPTV DHCP address.
- Use packet capture features only on networks and devices you are allowed to manage.

## License

MIT License. See [LICENSE](LICENSE).

## Disclaimer

This project is intended for home network learning, personal device management and troubleshooting of your own ISP-provisioned IPTV service. ISP network designs and firmware environments vary widely. Use it only where you are allowed to modify the relevant devices and network configuration.

## Donation

If you like this project, or if it helps you solve one small IPTV setup problem, you can use the Alipay payment QR code below to donate 15 CNY, buy me a milk tea and support future maintenance.

<p>
  <img src="assets/alipay-donate-15.jpg" alt="Alipay 15 CNY payment QR code" width="360">
</p>
