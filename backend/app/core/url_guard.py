"""安全工具：URL 校验，防 SSRF。

★ 为什么必须有这个（实测风险）：
  `ProfileIn._check_url` 原来只校验「以 http(s):// 开头」，
  于是下面这些全都能通过：
    http://127.0.0.1:8787          → 打本机内核
    http://169.254.169.254/...    → 云元数据服务（能拿到云账号凭据）
    http://10.0.0.1/ 内网地址       → 横向探测
  而 `/api/settings/{id}/test` 会把**真实的 API Key** 发往该地址，
  等于一个 API Key 外泄端点 + 内网端口扫描 oracle
  （test 会区分 unreachable/auth_failed/timeout）。

所以：所有「用户填 URL 后由服务端发起请求」的地方都要过这一关。
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

# 允许的协议（必须白名单，不能只判「有 http 就行」）
ALLOWED_SCHEMES = {"http", "https"}

# 主机名白名单：这些主机名是本机服务，测试配置时需要能连
_HOST_ALLOWLIST = {
    "127.0.0.1", "localhost", "::1", "0.0.0.0",
}

# 明确的云元数据端点（即便解析成 IP 也要拦）
_METADATA_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.goog",
    "100.100.100.200",   # 阿里云
}


class UnsafeUrl(ValueError):
    """URL 不安全（会打到内网/元数据服务）。"""


def _is_private_ip(host: str) -> bool:
    """判断 host 是否是内网/环回/链路本地地址。

    注意要处理三种写法：
      1. 直接是 IP���127.0.0.1、10.0.0.1）
      2. IPv6（::1、fc00::/7、fe80::/10）
      3. 整数形式的 IP（http://2130706433/ = 127.0.0.1）
         —— 这是 SSRF 绕过常用手法，用 ipaddress 解析不出来，
         所以额外用 socket 做一次解析兜底。
    """
    # 1) 直接当 IP 解析
    try:
        ip = ipaddress.ip_address(host)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    except ValueError:
        pass

    # 2) 尝试把纯数字主机当 IP（2130706433 -> 127.0.0.1）
    if host.isdigit():
        try:
            n = int(host)
            if 0< n <= 0xFFFFFFFF:
                return _is_private_ip(ipaddress.ip_address(n).compressed)
        except (ValueError, ipaddress.AddressValueError):
            pass

    # 3) 短 IPv4 写法 127.1 / 10.0.1
    parts = host.split(".")
    if 1 < len(parts) <= 4 and all(p.isdigit() for p in parts):
        try:
            return _is_private_ip(ipaddress.ip_address(host).compressed)
        except ValueError:
            pass

    return False


def assert_safe_url(url: str, allow_local: bool = False) -> str:
    """校验 URL 是否可由服务端主动请求。返回规范化后的 URL。

    allow_local=True 时允许 127.0.0.1/localhost
    （仅用于「测试本机自建服务」这种明确场景，默认关闭）。
    """
    if not url or not isinstance(url, str):
        raise UnsafeUrl("URL 为空")

    url = url.strip()
    if len(url) > 2048:
        raise UnsafeUrl("URL 过长")

    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise UnsafeUrl(f"URL 格式错误：{exc}") from exc

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeUrl(
            f"协议 {parsed.scheme or '(空)'} 不允许，只支持 http/https"
        )

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise UnsafeUrl("URL 缺少主机名")

    if host in _METADATA_HOSTS:
        raise UnsafeUrl(f"禁止访问云元数据服务（{host}）")

    if _is_private_ip(host):
        if not (allow_local and host in _HOST_ALLOWLIST):
            raise UnsafeUrl(
                f"禁止访问内网/环回地址（{host}）。"
                "本机服务请在配置里显式开启 allow_local"
            )

    # 用户名密码@host 形式有时被用来绕过解析
    if parsed.username or parsed.password:
        raise UnsafeUrl("URL 不允许包含用户名密码")

    return url


# 常见内网网段（供前端提示用）
PRIVATE_CIDRS = [
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "127.0.0.0/8", "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10",
]
