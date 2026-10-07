"""契约测试共用假适配器（形状完整，供 registry/contract 测试复用）"""


class FakeAdapter:
    name = "fake"
    kinds = {"video"}

    def match(self, url):
        return "fake.example" in url

    def resolve(self, q):
        return None

    def list_items(self, ref):
        return [ref]

    def fetch_meta(self, ref):
        return None

    def fetch_content(self, ref):
        return None

    def fetch_media_url(self, ref, media="audio", lowest=False):
        return None
