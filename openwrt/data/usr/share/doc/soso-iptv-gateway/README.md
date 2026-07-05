# SOSO IPTV Gateway

SOSO IPTV Gateway is an OpenWrt/HigoOS IPTV gateway management package for home IPTV multicast networks.

It provides:

- udpxy-based multicast-to-unicast playback
- IPTV interface/default-route repair tools
- M3U playlist generation and logo injection
- local logo cache under `/www/iptv/logos`
- local EPG generation under `/www/epg.xml.gz`
- a web management page at `/iptv/`

For installation and usage, see the public repository documentation:

- `README.md`
- `docs/INSTALL.md`
- `docs/TROUBLESHOOTING.md`
- `docs/SECURITY.md`

Default published packages contain no private router address, NAS address, account, password, or runtime playlist backup.
