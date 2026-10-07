import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from core.images import download_images


class _Resp:
    content = b"fake-jpg"
    def raise_for_status(self): pass


class _Sess:
    headers = {}
    def __init__(self, fail=False): self.fail = fail; self.hits = []
    def get(self, url, **kw):
        self.hits.append(url)
        if self.fail: raise RuntimeError("boom")
        return _Resp()


def test_download_images_saves_to_assets(tmp_path):
    s = _Sess()
    saved = download_images(["https://pic1.zhimg.com/a.jpg"], str(tmp_path), session=s)
    assert len(saved) == 1 and saved[0].startswith("assets/")
    assert os.path.exists(os.path.join(str(tmp_path), saved[0]))


def test_download_images_skips_failures(tmp_path):
    saved = download_images(["https://pic1.zhimg.com/a.jpg"], str(tmp_path),
                            session=_Sess(fail=True), attempts=1)
    assert saved == []


def test_download_images_skips_existing(tmp_path):
    s = _Sess()
    first = download_images(["https://pic1.zhimg.com/a.jpg"], str(tmp_path), session=s)
    second = download_images(["https://pic1.zhimg.com/a.jpg"], str(tmp_path), session=s)
    assert first == second and len(s.hits) == 1  # 第二次不重复请求
