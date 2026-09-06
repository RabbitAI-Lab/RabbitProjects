#!/usr/bin/env python3
"""Sprint-3 验收演示数据准备（幂等可重跑）。

建「S3 验收演示」项目并铺齐 14 幕录屏所需数据：救火看板卡片集（紧急/高/
多执行人/被阻塞/未指派）、自定义 select 字段（严重等级）、评论线程（折叠
余量/图片 asset/待删父）、活动历史（批量 epoch 组 + 单字段 + 软删 + 工时 +
复制）、第二用户（李四，CONTRIBUTOR，密码固定）及其个人视图、被移出成员
（王五，幕 13 错误分支）。数据准备走 API（不属于被验收场景本身），录屏场
景全部从登录 UI 出发操作。

用法：python3 scripts/seed_acceptance_s3.py [http://localhost:8000]
"""
from __future__ import annotations

import base64
import json
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "tests/jmeter")
from _contract import Client, HTTP, q  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
TAG = "S3 验收演示"
IDENT = "S3AC"
WS = "workspace"
MINIO = "http://localhost:9000"
LISI = {"email": "lisi@rabbit.dev", "password": "Rabbit123!", "name": "李四"}
WANGWU = {"email": "wangwu@rabbit.dev", "password": "Rabbit123!", "name": "王五"}

# 演示图（Pillow 预生成，与 acceptance_video_s3.mjs 内嵌同一份字节）
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAeAAAAEOCAIAAADe+FMwAAAF60lEQVR42u3dPYpUURCG4ffK7GEQRQSZyEBBTMTIxD2YGogigiaCCxgwURAR"
    "XIqJkZiIMMlEIoj4g7iIcQUG0rT3nr7Ps4H+OEF1zelTNdPB7d8BsDx7deIUABbolCMAUKABUKABxrc3OQMAHTQA//KKQwsNoIMGwB00gEEV"
    "AHLFAaBAA6BAA9BffiT0KyGADhoABRpAgQYggyoABlUAyBUHALbZAeigAfAjIcAO/0i4Lcev950vsAYX7/zKFQdA7qAByLIkgKFtqZAaVAHY"
    "3IkrDoDcQQOgQAOQQRWALZh00ACWJQEwfwutgwZwBw2ggTaoApBBFQDyigNAgQYg2+wAss1OBw2QKw4AFGiAHTVduvvDKcAKHb06PddHX773"
    "0/lnUAVoNWMdueIAwDY7IKsrdNAA2GYHaKAdgQ4aQIEGQIEGyLIkgEZaLWRQBSCDKrniAFCgAVCgAcigCpBBlbF/JIRl+fjyzFwffeX+d+eP"
    "ZUmgtXPO5A4awB00aOxwzgZVIAMUzjlXHAAo0AAo0ADZZgfZsoZz1kEDmCQE+O8+vDg710dfffBNBw2AQRXIAIVzXnRmgyqQAQrnvMzMrjgA"
    "ss0O/B2LOw4dNIAfCUFjhwZaBw2QSUIAFGgAsiwJssTHOY+U2aAKZFDFORtUAUCBBlCgAcigCmSAwjlnUAUAy5JAa+ecZdZBA7iDhpU1du+f"
    "n5sr87WHXzWj+Y8qkAEKmWXOFQdA3kEDoEADkG12kC1rMg+eWQcNkCsOABRogAyqQAZVZJbZoMrc3j07P9dHX3/0xflngELmDKoAkG122LLm"
    "nGWWWQcN4EdCNBzOWWaZddAAeQcNgAINQJYlZVELlvjIvNTMBlXySB4DFDIbVAFAgQZQoAHIoEoGKHDOMmdQBQDLktBCO2eZZdZBA7iDRsPh"
    "nGWW2aBKBigwQCFzBlUAUKABUKABss2ObLNzzjLLrIMGyBUHAAo0QAZVyCN55yyzzAZVyKCKc5ZZ5lxxAGSbHe44nLPMMm96xcEue/v0wlwf"
    "fePxZ+cPfiTU2Mkss8z5jyoAKNAACjQAWZZElsvILLPMBlUyQCGzzDJnUAUABRpAgQYggyrkYb/MMsusgwawLAkth8wyy6yDBnAHjYZDZpll"
    "NqhCHvbLLHMGVQDIO2gAFGiAbLMj279kllnmXfmfhG8OD+b66JtPPqn4QK44AFCgATKokqEPmWWWWWaDKnkkL7PMMmdQBYBss/P3lcwyyyyz"
    "DhrAj4S+CGWWWWaZddAAeQcNgAINQKtelmRRi8wyyzzKsiSDKjLLLLPMBlUAUKABFGgAMqiSR/IyyyxzBlUAsCzJ17fMMssssw4awB20L0KZ"
    "ZZZZZoMqeSQvs8wyZ1AFAAUaQIEGINvssklLZpllllkHDZArDgAUaIAMqmRQRWaZZZbZoEoeycsss8wy54oDINvs/K0is8wyy6yDBvAjoQZa"
    "ZpllllkHDZB30AAo0ABkWVIWtcgss8wjZzaoIrPMMstsUAUABRpAgQYggyp5JC+zzDJnUAUAy5J8fcsss8wy66AB3EH7IpRZZpllNqiSR/Iy"
    "yyxzBlUAUKABUKABss0um7RklllmmXXQALniAECBBsigSgZVZJZZZpkNquSRvMwyyyxzrjgAss3O3yoyyyyzzDpoAD8SaqBllllmmXXQAHkH"
    "DYACDUCWJWVRi8wyyzx4ZoMqMssss8wGVQBQoAEUaAAyqJJH8jLLLHMGVQCwLMnXt8wyyyyzDhrAHbQvQplllllmgyp5JC+zzDJnUAUABRoA"
    "BRog2+yySUtmmWWWWQcNkCsOABRogAyqZFBFZpllltmgSh7JyyyzzDLnigMg2+z8rSKzzDLLrIMG8COhBlpmmWWWWQcNsHrTrcMjpwCQQRUA"
    "siwJYPBndmscVAHIFQcACjSAAg1ABlUAdNAAWJYEgA4awB00AAZVAHLFAYACDYACDZBtdgDooAEUaAAUaIAMqgBgUAWAXHEA2GYHgA4awI+E"
    "AOigAVCgARRoADbzB3fUs/GEa8CSAAAAAElFTkSuQmCC"
)
GIF_B64 = (
    "R0lGODlh4AEOAYEAAPD1/OLo8DuC9u9ERCH/C05FVFNDQVBFMi4wAwEAAAAh+QQAMgAAACwAAAAA4AEOAQAI/wADCBxIsKDBgwgTKlzIsKHD"
    "hxAjSpxIsaLFixgzatzIsaNHiABCihxJsqTJkyIDoFzJEqXKljBhvoxJ8+TMmjhT5twZ8iZPmj5/yhSKMyjRlUaP2lQaMylTkk6f6pSKlGpV"
    "q0uxmoxKlatUr0/BMhWrlOxRs0TRClX7ky1Ptzvh5pRbVOtWuyXp1tQLFC9UvyP5NgU8lbDgoYQBHG65mGXjq4YTK5b82CXly4krZ42cGTPn"
    "z4A13+1MGrRf0Xk9h1Z9mjVe1H9Lr5bdmvZr13ZhB8atVXfh2aZv287NG6vvnsWtHp88vHfyrs+/Rg87fWz1stfPZk+7fW33tt/fhv+PO35u"
    "+brNjZ/fu75veuXtB7+HPl96fer3refHvl97f+7/eRcgeAOKVyB5B5qXIHrBEbcgew+616BzEco3oXoVInYhfBkyFp+GwIVY24b0kWififih"
    "qJ+K/LHon4sAwiigjATSaKCNCOKooI4MiigcjxACKaGPDgppIZEUGgniiEhiqKSHHTr2IZRPShklZE1yWCWWTHb5Y5YlgnmimCmSuaKZLaL5"
    "opoxsjmjmzXCeaOcOdK5o509elkknkHyOaSevX0k6KCEFmrooYgmquiijDI6pZVbWnalpJFu5ueRgDp56ZJfZqrlplSCCqmoXHZq6p6ehpnq"
    "mKuW2eqZr6b/Geuas7ZZ65u3xpnrnLvW2eudv+Z5apKkUlqspcH2meyfw2q6LKbNfkqTANRWa+212Gar7bW+Rquqt6yC66q4sJIra0zbpquu"
    "ut2iai6t79oaL67z6lrvuvjmS227xD7LqbsA91svrwPzy5K+CKdrsLQFA9uwsAE7+7Cy9yZsMbcOR8ywxt9yHK7H44JcLkwXl7xvxgKLfK7K"
    "8LIsr8v0wgyAySUv3HHKOEssM8E72/xxzivRfLHPIQN9s85G/4z00hsnbZLQFhM9stNTM31001YrjfXWKEGdsNQrUx121kWTXTXXWl/dtdf6"
    "gt2y2G+bPTbaZdN9ttonsd02ynLH/2333HjXHfjdaRf+tN74uv0y3Iv33fjffg8OuOElIZ4435A/HvgAnHfu+eeghy7655MLTnnphJt+uOUK"
    "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"
    "gppYhqMYaIW4e4dIiYnIhW6IiH/IgXhofbp4i5iYi6W4i7iYghMYjJ44i3v4in3Ii4IYi8N3fIjyfAlhjQiBjQehjQbBjQXhjRkRfUHYjMzo"
    "iM7IiOUIiukoioFohOcIiZwoiV3Yjnm4jq5oj7D4jg8IhkA4Z+xnjMPYi8o4kMUYg/0YcCEYi2lIj6SokKroNeLoj2EHkKtIjA45j9Dojvjo"
    "feTIkGd4kRQJkRapj7LokR2ZkT1njhuJjiapki2pji/JjijZkCTZeKhYjzH/eY85mY8r2YM1+ZBQw4o9uYk0OZQlOZMf+ZMYeYzRqJQReJA+"
    "aZQLiZQnyZQauZPPaJU4SZXol5RSCZRCI5RYyZJcCZNlKZNaiXRoGZBN+ZVLyZZXeZY6KZc8OZbwWJR2uY8FuYx5eZRp6ZV9aZOR2JaBCZY0"
    "I5Z0mZVwuZV/WZWLqZZz2ZgumZhkKZlmaZlrWZECuZf8iIB4SZlRWZhvqZmECZp6CZIG6ZmAaZp++ZiriZmR6ZqOSZpxCZt1yZqEeJmyOZm2"
    "qZi0yZi7qZu/+Zm9WZnB2WSlWZyhyZpTqZyn6ZTCOJy0KJwiuZmoyZfMaZgmg5jOWXfWCZ3Y2Z3N/3mcsSmds1mdX/ecbhmSQTmS6xmd6Fmb"
    "5HmH7ima7BmW9ZmdoxmfwGmeypEo4DgQASoQAxoABXqgxvZXCRpYiTGe/kmd7fmd7xme8+mb/AmZtyme2lkz+amh+xmhyVmhpHeeICqfD5qZ"
    "F/qaIrqcHnqfh/lc5ZmiJIqfEmqf8Fmi/SmjvLmiRKmiJxqjOEqcPNqaP5qhQ+qgOvqDNaqfLrqdHXqkGzo0T1qkHLmjVGqcV8qiUPqhNBqi"
    "WaqefZmbKBqkPpqkY9qlJmqmQIqmOUqmM/qi9YakbmqlamqkX0qkdWqhcwqhbIqhetqnZbqnZwqnXpqnWGqoWnqnghmPhf8qqGtKqGnqqHaK"
    "qGDKpDcKqFMnpIoapVEzpZSKp5L6p5DappjalW/qpEvaopc6qpr6qXJaqkraqLA6qKgqq6waqLP6qLUaqbk6qaE6onT6q4nqqpz6NZ4qrJWq"
    "qr4Ilcm6pU3KoanqrKu6q6R6q5mKq9YarL0qqtTaqsgKqtt6qN+6qOn3E6+arXyKrrQKrbbardjqrqfKrmvoq+E6rONarAjDndJKoXcqproq"
    "r9UKr9qqrv8qpdG6qVxKsPR5sMSasAKbrg+7rgbbrgDrrfXao/E6sbyqsNxase/qsRnbqQx7r6Y4mBsbsQUrshSrsQELsgOLsvTKseJ6sW0B"
    "oAv/Gmw3O2w5i3w4pHw72xAImnyJIhnnCrMdy7IWK7P2SrNF67KxerJOK7EqC7VI+7FVG7LGOrJMG3+qibX5eqxb67BRm7JZu7JT27JXa6ov"
    "O7Yxa7Qzq7TNirDPmrYQy7ZVWrd0K7VlS7Vnm7Ruu7RwC66BS64eSBRNm7dk+7VaO7j4ujeL+7cYu7aI27Z2+7aQK7iXe7h9a7Wbe61e67hm"
    "u7do27mSS7p4a7p6q7ihq7rzerSom7igy7ei67eVC7iZ27j5EpGWW7tx27Bz+7qUO7muO7ucS7yeW7rGi7ysO7rJe7rNm7qxy7zLS7vCC6zO"
    "O73Fi72fm7tgy7hiW722/8u73rm60Uu9wDu82qu85Zu967u9l/O44uuvwXu+uwu+vUuyv/u8sMu98Gu/4yu76Xu97au+/Eu+BQzAAyzAByy9"
    "CYycCLzA5qu/8yvB6NvA+/u+BozBD6zBKanAHBzBAQy9EMy+I+y+66KvcpuaNwnCFjzBIXzBJ9y9t/u99Bu+/iu/FVzCBPzBJMzDJuw7/VvD"
    "9xu2T9m1OxzDQUzB9SvEmCu+mqvENszEOLzEUDzE3pu/L+zCLZzDPnzEQJyeTey/T5zFXIzEGWzGG4zGDKzDT7vGXezBaszCbCzCb0zHcdzD"
    "d/zDrWMYNiu0evWzzAfIzifIChG0PaugfnwoRP+Lu3UMw1+cxo/sxnnsxXt8xpHsp1RMxpm8xZs8x45cyZAMypJ8yXhMyscLx6ZMyeCTxJoc"
    "xVUcxkw8xpx8l6UsynLcyFrsybmMy2Wcyqhsy7W8ymAsy7rcy8Csx8Icysk8ysesytqiu67cylY8w1g8y9PsxIw8yW18y9psx77szc38y8vM"
    "zd/8yeMczM8Mo7vczeaczqxszbD8ysTMy51Mz7SMzO5syeEMzueMz9mCwr47rfHbZ/PMzutczge9z+38zzKMzSXLqMqczxHN0O9czPVs0Mbc"
    "z85M0fqs0duMzhw90dgC0Pgr0GKczQh9t/ws0czs0Ssd0i3N0uSs0An/7dILPdLqnNEyDdI4XdH2fM0nTcPyzLUrXLiRu3dqZ7IQvdTlOsVg"
    "p7ZIvbBFjYUqzYYOzNRG/b9YTdXWu3lJvdW1eNReLdVK3dS/OJ1jXdXjCNVp3dVW/dVmzaz21roIiclP/dFcfc9hrdVxbcRRrdYSydZvTdZg"
    "/dduvdZ4vddOXX1oPdiAXdeNjdhX3ddTrdhnfcptrdeGrdmZLdaOfdiBndib7dmSDddZvdhz3cGdzden7WjU+NqwHduyPdu0LSjQzNiY/dmc"
    "rdukHdqT3dpyXYG5XdqETdllDdx+vdqoLdyCTdyPfde/nde9DdnD7dumLd2sjd3Lfdupbde4/93c1l3cyF3Zo53dlh3c3M3coq3clw3e1O3e"
    "0H3d553cvG3e5b3dOf3e613f+I2MYKze0T3f5M3e6J3f8S3e2t3e++3coK3fAX7fCv7gBE7fDL7bFT7dB/7c373g4a3h3R3ZHd7gGS7iGy7h"
    "/B3h8g3hBe7fdD3iFh7iL+7gKT7hA37iK/7f6Z3jBl7iM27jFA7jGM7jCC7gx53gN97iQu7hAN7jF27fNF7kRF7YTy7lPl7jTd7fOL7jH17d"
    "Mj7kKv7jXa7kOs7iql3lUP7lVg7kTm7mVH7lKO7lU27cba7mWI7kWw7fSU7id87hYa7nSw7nbC7ngj7eZx7nhD7nfXYe4y4e5Htu4m5+5GX+"
    "6GC+6Gsu6Wme6Iz+52Ku5Zru52Oe5WTu3Y0eFrVd6qZ+6qie6r326Xbe6Yqe568+6oBu6YUe6Ic+6EY+6bCe6awe6XT+5pse6iCO6ZX+65Au"
    "6q7O65ze68jO7MNO6XXu68Qe7c2eGAEBACH5BAEyAAQALDYALAAZASkAgfD1/OLo8DuC9u9ERAj/AAEIHEiwoMGCAQ4eJMCwocOHECNKnEix"
    "osWLGDNq3Mix48QBIEOKHEmypMmRClOmTKhyoMeXMGPKnEmzZsWTOHPmFGhzIs+eQIMKHUr044CiDEH+LLoUqdOnUKNCBOkUZIAATq9K3cq1"
    "a0+qSJUCcNrUq9mzaI1WHVCW4kKMbdPKncsV7MWSGcVabAkAa8W4dAMLHmrXaE6Let3yTfh37ODHkIEWjqgz5E22jiPyHcgyM0TAkUOLxjjZ"
    "YWWSRgFv5kxQIujRsGObPkr5NOracVezbv3Zs+zfwEsTsG0St2+GugV2zn0cuHPRwonjnYq5d/LlbV8/3063tPTiDxM3/0y+u+BD7dzTn538"
    "/WT46g7JK387vrn6++tpN2wPfj/8+uRhVxZ6+BX4FHv83eZfU/LNRx8BBBooIWH6DZfgdEn9B2GDAjJo34QgElXYhRha6KF8HXoWYYgsyjQi"
    "iSPNdmKACsX3YYs4zvQijJYtqCKHNdaX45CSVcijSDL+iGKQyN1I5JMa7XhkkjYu+eCKUGZZ22xHGqnhhjQadJ6TWpa5JZcwvjeglebZaOab"
    "pFVoIY9q+gakmG7CqadaaJJYp3W6CdjbnoRSR52f72kF0VWMNuroo4xGpGihlEZ3oXGaXdfmmJR2ammCmGa62nKukdnpk8LN+Z1E4gHaUmc+"
    "mWV66pCpqmpbak5uxphisxJaa4aVXYbeYnvJ2muLvwKLE2JfKrZpscfqmWyf0/pIlrHRgliti80GhWW2Em4bU6tDfQtugeLCRK5Q5p57X7ov"
    "idUgQqu5q2WXp81rUIp42osqvjoFBAA7"
)


def _png_bytes() -> bytes:
    return base64.b64decode(PNG_B64)


def _gif_bytes() -> bytes:
    return base64.b64decode(GIF_B64)


def put_minio(upload_path: str, content_type: str, body: bytes) -> None:
    """presign 返回 /uploads/<bucket>/<key>?sig → 直连 MinIO :9000 PUT（签名按 9000 端口计算）。"""
    assert upload_path.startswith("/uploads/")
    url = MINIO + upload_path[len("/uploads"):]
    r = urllib.request.Request(url, data=body, method="PUT",
                               headers={"Content-Type": content_type})
    with urllib.request.urlopen(r, timeout=15) as resp:
        assert resp.status == 200, (resp.status, url)


def ensure_user(admin: Client, who: dict) -> Client:
    """幂等：已有则登录；没有则邀请（admin）→ 注册（自动接受）→ 返回其会话。"""
    c = Client(BASE)
    code, _ = c.req("POST", "/api/v1/auth/sign-in/",
                    {"email": who["email"], "password": who["password"]},
                    {"X-CSRFToken": c.csrf()})
    if code == HTTP["OK"]:
        return c
    admin.req("POST", f"/api/v1/workspaces/{q(WS)}/invitations/",
              {"emails": [who["email"]], "role": 10},
              {"X-CSRFToken": admin.csrf()})
    code, body = c.req("POST", "/api/v1/auth/sign-up/",
                       {"email": who["email"], "password": who["password"],
                        "display_name": who["name"]},
                       {"X-CSRFToken": c.csrf()})
    assert code == HTTP["CREATED"], (who["email"], code, body)
    return c


def main() -> None:
    admin = Client(BASE)
    code, body = admin.req("POST", "/api/v1/auth/sign-in/",
                           {"email": "zhangsan@rabbit.dev", "password": "Rabbit123"},
                           {"X-CSRFToken": admin.csrf()})
    assert code == HTTP["OK"], (code, body)
    me = admin.req("GET", "/api/v1/users/me/")[1]["data"]
    zhangsan_id = me["user"]["id"]

    lisi_c = ensure_user(admin, LISI)
    wangwu_c = ensure_user(admin, WANGWU)
    lisi_id = lisi_c.req("GET", "/api/v1/users/me/")[1]["data"]["user"]["id"]
    wangwu_id = wangwu_c.req("GET", "/api/v1/users/me/")[1]["data"]["user"]["id"]

    # ── 幂等清场：先删旧 S3AC 项目内字段（项目软删不级联字段——不先删会
    #    累积孤儿启用字段，挤满 BR-10 的 50/WS 上限且 API 不可达），再删项目 ──
    for prj in admin.req("GET", f"/api/v1/workspaces/{q(WS)}/projects/")[1]["data"]:
        if prj.get("identifier") != IDENT:
            continue
        old_base = f"/api/v1/workspaces/{q(WS)}/projects/{prj['id']}"
        for d in (admin.req("GET", old_base + "/issue-properties/?scope=all")[1]["data"] or []):
            admin.req("DELETE", f"{old_base}/issue-properties/{d['id']}/",
                      None, {"X-CSRFToken": admin.csrf()})
        admin.req("DELETE", f"{old_base}/", None, {"X-CSRFToken": admin.csrf()})

    code, body = admin.req("POST", f"/api/v1/workspaces/{q(WS)}/projects/",
                           {"name": TAG, "identifier": IDENT},
                           {"X-CSRFToken": admin.csrf()})
    assert code == HTTP["CREATED"], (code, body)
    proj = body["data"]["id"]
    base = f"/api/v1/workspaces/{q(WS)}/projects/{proj}"

    st = admin.req("GET", base + "/states/?include_cancelled=1")[1]["data"]
    groups = {s["group"]: s["id"] for s in st}

    # ── 成员：李四 CONTRIBUTOR；王五 加入后移出（幕 13B 错误分支状态）──
    for uid in (lisi_id, wangwu_id):
        code, b = admin.req("POST", base + "/members/", {"member_ids": [uid], "role": 15},
                            {"X-CSRFToken": admin.csrf()})
        assert code in (HTTP["OK"], HTTP["CREATED"]), (code, b)
    pm = admin.req("GET", base + "/members/?per_page=100")[1]["data"]
    wangwu_pm = next((m["id"] for m in pm if m["user"]["id"] == wangwu_id), None)
    if wangwu_pm:
        admin.req("DELETE", f"{base}/members/{wangwu_pm}/", None,
                  {"X-CSRFToken": admin.csrf()})

    # ── 标签（分组维度素材）─────────────────────────────────────────
    labels = {}
    for name, color in (("前端", "#3B82F6"), ("联调", "#10B981"), ("回归", "#F59E0B")):
        c2, b2 = admin.req("POST", base + "/labels/", {"name": name, "color": color},
                           {"X-CSRFToken": admin.csrf()})
        assert c2 == HTTP["CREATED"], (name, c2, b2)
        labels[name] = b2["data"]["id"]

    def mk(name, parent=None, **f):
        path = base + (f"/issues/{parent}/sub-issues/" if parent else "/issues/")
        c3, b3 = admin.req("POST", path, {"name": name, **f}, {"X-CSRFToken": admin.csrf()})
        assert c3 == HTTP["CREATED"], (name, c3, b3)
        return b3["data"]

    def assign(issue_id, ids):
        c4, b4 = admin.req("PUT", f"{base}/issues/{issue_id}/assignees/",
                           {"assignee_ids": ids}, {"X-CSRFToken": admin.csrf()})
        assert c4 == HTTP["OK"], (issue_id, c4, b4)

    today = time.strftime("%Y-%m-%d")

    # ── 救火看板卡片集（幕 1/2/3）────────────────────────────────────
    multi = mk("S3-紧急-多执行人", priority="urgent", target_date=today,
               label_ids=[labels["前端"], labels["联调"]])
    assign(multi["id"], [zhangsan_id, lisi_id])
    z_urgent = mk("S3-紧急-网关超时", priority="urgent", target_date=today)
    assign(z_urgent["id"], [zhangsan_id])
    l_high = mk("S3-高-联调阻塞排查", priority="high", target_date=today)
    assign(l_high["id"], [lisi_id])
    z_high = mk("S3-高-发布清单", priority="high", target_date=today)
    assign(z_high["id"], [zhangsan_id])
    mk("S3-中-文案修订", priority="medium")
    mk("S3-中-样式走查", priority="medium")
    mk("S3-低-文档补充", priority="low")
    mk("S3-低-日志清理", priority="low")
    mk("S3-无优先级-甲")
    mk("S3-无优先级-乙")
    mk("S3-无优先级-丙")
    mk("S3-未指派-待认领甲")
    mk("S3-未指派-待认领乙")

    # ── 被阻塞链（幕 5 流转守卫拦截）────────────────────────────────
    pre = mk("S3-前置-账号服务迁移", state_id=groups["started"], priority="high")
    blocked = mk("S3-被阻塞-登录联调", priority="high", target_date=today)
    c5, b5 = admin.req("POST", f"{base}/issues/{blocked['id']}/relations/",
                       {"related_issue_id": pre["id"], "relation_type": "is_blocked_by"},
                       {"X-CSRFToken": admin.csrf()})
    assert c5 == HTTP["CREATED"], (c5, b5)

    # ── 含子任务卡 ×2（幕 6 批量删除级联）──────────────────────────
    for n in ("S3-删除组A", "S3-删除组B"):
        root = mk(n, priority="low")
        mk(f"{n}-子任务1", parent=root["id"])
        mk(f"{n}-子任务2", parent=root["id"])

    # ── 评论宿主 + 实时演示卡 + 批量池（幕 7~11）────────────────────
    host = mk("S3-评论演示任务", priority="high", target_date=today)
    assign(host["id"], [zhangsan_id])
    rt = mk("S3-实时演示卡", state_id=groups["unstarted"])
    bulk_pool = [mk(f"S3-批量池{i:02d}", priority="low") for i in range(1, 13)]

    # ── 自定义 select 字段（幕 3 按严重等级分组）────────────────────
    c6, b6 = admin.req("POST", base + "/issue-properties/",
                       {"name": "严重等级", "field_key": "cf_sev3", "field_type": "select",
                        "required": False,
                        "options": [{"value": "critical", "label": "致命", "color": "#DC2626"},
                                    {"value": "major", "label": "严重", "color": "#F59E0B"},
                                    {"value": "minor", "label": "一般", "color": "#3B82F6"}]},
                       {"X-CSRFToken": admin.csrf()})
    assert c6 == HTTP["CREATED"], (c6, b6)
    for issue_id, sev in ((multi["id"], "critical"), (blocked["id"], "critical"),
                          (z_urgent["id"], "major"), (l_high["id"], "major"),
                          (z_high["id"], "minor"), (host["id"], "major")):
        c7, b7 = admin.req("PATCH", f"{base}/issues/{issue_id}/",
                           {"custom_fields": {"cf_sev3": sev}},
                           {"X-CSRFToken": admin.csrf()})
        assert c7 == HTTP["OK"], (issue_id, c7, b7)

    # ── 评论线程（幕 7/8/9）─────────────────────────────────────────
    def comment(html, parent=None, actor=None):
        cli = actor or admin
        payload = {"comment_html": html}
        if parent:
            payload["parent_id"] = parent
        c8, b8 = cli.req("POST", f"{base}/issues/{host['id']}/comments/", payload,
                         {"X-CSRFToken": cli.csrf()})
        assert c8 == HTTP["CREATED"], (html[:20], c8, b8)
        return b8["data"]

    # 幕 9：待删父 + 2 回复（父删子留）
    t1 = comment("这个方案需要再评估一下风险面，先别合入主干")
    comment("风险点主要在权限收敛，我补了用例", parent=t1["id"], actor=lisi_c)
    comment("用例链接发我一份，我下午过一遍", parent=t1["id"])

    # 幕 7：折叠线程（5 回复，其中 1 条「回复的回复」归并回顶层并带 @徽标）+ 表情预置
    t2 = comment("接口超时集中在网关重启窗口，建议把健康检查间隔调大到 15s")
    r1 = comment("同意，昨晚的告警也是这个时段", parent=t2["id"], actor=lisi_c)
    r2 = comment("我这边出了一份重启窗口的分布统计", parent=t2["id"])
    comment("分布统计里 3 点那次是计划内发布", parent=t2["id"], actor=lisi_c)
    comment("计划内发布建议走低峰公告", parent=t2["id"])
    # 回复的回复：parent 指向回复 r2（张三所发）→ 服务端归并挂 t2 + reply_to_actor=张三
    comment("统计表我看完了，公告模板我来提供", parent=r2["id"], actor=lisi_c)
    # 表情（张三 🎉 + 李四 🎉 → 名单浮层含（你）；另给 t1 预置 👀）
    def react(cid, emoji, actor):
        c9, b9 = actor.req("POST", f"{base}/issues/{host['id']}/comments/{cid}/reactions/",
                           {"emoji": emoji}, {"X-CSRFToken": actor.csrf()})
        assert c9 == HTTP["OK"], (emoji, c9, b9)
    react(t2["id"], "🎉", admin)
    react(t2["id"], "🎉", lisi_c)
    react(r1["id"], "👀", lisi_c)
    react(r2["id"], "👍", admin)

    # 幕 8：图片评论（presign entity_type=comment_image → 直传 MinIO → complete）
    png, gif = _png_bytes(), _gif_bytes()
    assets = []
    for fname, mime, blob in (("s3-demo-shot.png", "image/png", png),
                              ("s3-demo-anim.gif", "image/gif", gif)):
        c10, b10 = admin.req("POST", f"{base}/issues/{host['id']}/attachments/presign/",
                             {"file_name": fname, "content_type": mime,
                              "file_size": len(blob), "entity_type": "comment_image"},
                             {"X-CSRFToken": admin.csrf()})
        assert c10 == HTTP["CREATED"], (fname, c10, b10)
        pres = b10["data"]
        put_minio(pres["upload_url"], pres["fields"]["Content-Type"], blob)
        c11, b11 = admin.req("POST", f"{base}/issues/{host['id']}/attachments/{pres['asset_id']}/complete/",
                             {"etag": "", "size": len(blob)},
                             {"X-CSRFToken": admin.csrf()})
        assert c11 == HTTP["OK"], (fname, c11, b11)
        assets.append((pres["asset_id"], fname))
    # alt 必须是文件名（parseCommentImages 以 .gif 后缀识别 GIF 角标——与 UI 上传路径同口径）
    img_html = "线上复现的两张截图（图 1 趋势 / 图 2 动效）：" + "".join(
        f'<img src="/api/v1/workspaces/{WS}/projects/{proj}/issues/{host["id"]}/attachments/{a}/download/?variant=thumb" alt="{fname}"/>'
        for a, fname in assets)
    comment(img_html)

    # ── 活动历史（幕 10）────────────────────────────────────────────
    # ① 批量 epoch 组：一次 bulk 12 条（共享 epoch → 动态流折叠 batch 行）
    c12, b12 = admin.req("PATCH", base + "/issues/bulk/",
                         {"issue_ids": [i["id"] for i in bulk_pool],
                          "patch": {"state_id": groups["started"]}},
                         {"X-CSRFToken": admin.csrf()})
    assert c12 == HTTP["OK"], (c12, b12)
    # ② 多条单字段变更
    admin.req("PATCH", f"{base}/issues/{z_urgent['id']}/",
              {"priority": "medium"}, {"X-CSRFToken": admin.csrf()})
    admin.req("PATCH", f"{base}/issues/{z_urgent['id']}/",
              {"priority": "urgent", "estimate_minutes": 480},
              {"X-CSRFToken": admin.csrf()})
    admin.req("PATCH", f"{base}/issues/{l_high['id']}/",
              {"target_date": today}, {"X-CSRFToken": admin.csrf()})
    # ③ 工时 + 复制
    admin.req("POST", f"{base}/issues/{z_high['id']}/worklogs/",
              {"minutes": 90, "worked_on": today, "note": "发布清单逐项核对"},
              {"X-CSRFToken": admin.csrf()})
    admin.req("POST", f"{base}/issues/{host['id']}/duplicate/",
              {"include_subtrees": False, "include_assignees": False,
               "include_labels": False, "include_custom_fields": False,
               "include_dates": False},
              {"X-CSRFToken": admin.csrf()})
    # ④ 软删任务（动态流置灰 chip 素材）
    victim = mk("S3-已删除的旧需求")
    assign(victim["id"], [zhangsan_id])
    admin.req("DELETE", f"{base}/issues/{victim['id']}/", None,
              {"X-CSRFToken": admin.csrf()})

    # ── 个人视图（幕 1 救火看板由录屏创建；幕 13 越权素材）──────────
    def mk_view(cli, name, layout="kanban"):
        c13, b13 = cli.req("POST", base + "/views/",
                           {"name": name, "layout": layout,
                            "filters": {"op": "AND", "conditions": []},
                            "display_props": {"group_by": "state_id", "order_by": "sort_order"}},
                           {"X-CSRFToken": cli.csrf()})
        assert c13 == HTTP["CREATED"], (name, c13, b13)
        return b13["data"]

    z_view = mk_view(admin, "张三-私人视图")
    l_view = mk_view(lisi_c, "李四-工作台")

    # ── 等活动异步收敛（worker 落库；超时只警示不失败）───────────────
    deadline = time.time() + 20
    ok_batch = ok_dead = False
    while time.time() < deadline and not (ok_batch and ok_dead):
        rows = admin.req("GET", base + "/activities/?per_page=50")[1].get("data") or []
        ok_batch = ok_batch or any(r.get("kind") == "batch" and r.get("batch_count") == 12 for r in rows)
        ok_dead = ok_dead or any(
            (r.get("issue") or {}).get("issue_key") == victim["issue_key"]
            and (r.get("issue") or {}).get("is_deleted")
            for r in rows)
        if not (ok_batch and ok_dead):
            time.sleep(0.8)
    if not (ok_batch and ok_dead):
        print(f"⚠ 活动收敛等待超时（batch={ok_batch} deleted={ok_dead}）——worker 可能滞后")

    print(f"✓ 数据就绪：WS={WS} 项目={TAG}（{IDENT}，{proj}）")
    print(f"  幕内任务名前缀「S3-」；李四 {LISI['email']}/{LISI['password']}（CONTRIBUTOR）")
    print(f"  王五 {WANGWU['email']}/{WANGWU['password']}（已被移出项目——幕 13B 直入动态页 404）")
    print(f"  张三个人视图（幕 13 越权 URL）：/{WS}/projects/{proj}/board?view_id={z_view['id']}")
    print(f"  李四个人视图：/{WS}/projects/{proj}/board?view_id={l_view['id']}")
    print(f"  评论宿主 {host['issue_key']}（线程 ×3：待删父/折叠 5 回复/图片 ×2）")


if __name__ == "__main__":
    main()
