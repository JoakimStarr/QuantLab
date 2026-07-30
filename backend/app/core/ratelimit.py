"""登录限流：基于 slowapi 的 IP 维度限流。"""
from slowapi import Limiter
from slowapi.util import get_remote_address

# 全局限流器实例，按客户端 IP 限流
limiter = Limiter(key_func=get_remote_address)
