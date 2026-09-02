"""本地开发配置。"""

from .common import *  # noqa: F401,F403
from .common import env

DEBUG = env.bool("DEBUG", default=True)
