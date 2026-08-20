# Core/tools/haismart/vendor/__init__.py
#
# VENDORED CODE — not FRED's own. This directory holds an unmodified copy
# of two portable, framework-agnostic packages from
# https://github.com/enapt/haismart-local (MIT licensed, see LICENSE in
# this directory), pinned at commit 8e78351ec481ec9611b6d1348cd0be9aaedd9d9f
# (2026-08-17):
#
#   haismart_hrdp/       the local TCP protocol client (AES/localKey
#                         encryption, command framing, status parsing) for
#                         Haier's Haismart/U+ AC line. Talks to the AC
#                         directly on the LAN — no cloud involved.
#   haismart_extractor/  the one-time cloud client used ONLY by the setup
#                         script (Core/tools/haismart_setup.py) to log in
#                         with a Haier account and fetch each AC's
#                         per-device localKey. Never touched again after
#                         setup.
#
# Not on PyPI (confirmed 2026-08-20 — no `haismart-hrdp` or
# `haismart-extractor` distribution exists), so pip-installing isn't an
# option. Both packages are already Home-Assistant-agnostic by design
# (that's their whole reason to exist as separate packages from
# custom_components/haismart/), and reimplementing Haier's binary wire
# protocol and AES framing from scratch would be reckless when a tested,
# hardware-confirmed implementation already exists — so this vendors them
# verbatim rather than rewriting.
#
# Nothing in here is edited. If enapt/haismart-local ships a fix, replace
# these two directories wholesale rather than patching in place.
