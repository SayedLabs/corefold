"""Cloud "Copy a share link": each service is asked for a see-only link anyone can open, Dropbox's own link
is used again when the file has one, and a folder on this PC (or Shared with me) has no link to give."""
import pytest

from corefold import cloud


class Recorder:
    """Stands in for the service: answers by address, and writes down what was asked."""

    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def __call__(self, client, method, url, **kw):
        self.asked.append((method, url.split("?")[0].rsplit("/", 2)[-2:], kw.get("data")))
        for part, answer in self.answers:
            if part in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return {}

    @property
    def method(self):
        return lambda client, method, url, **kw: self(client, method, url, **kw)


def _acc(service):
    return cloud.Account(service, "me@example.com", refresh="r", access="a", expires=9e12)


def test_google_drive_lets_anyone_with_the_link_see_it(monkeypatch):
    rec = Recorder([("/permissions", {"id": "p"}), ("/files/abc", {"webViewLink": "https://drive.google.com/x"})])
    monkeypatch.setattr(cloud.Client, "call", rec.method)
    e = cloud.Entry("report.pdf", False, "abc", "/")
    assert cloud.GoogleDrive(_acc("gdrive")).share_link(e) == "https://drive.google.com/x"
    method, _, data = rec.asked[0]
    assert method == "POST" and data == {"role": "reader", "type": "anyone", "allowFileDiscovery": False}


def test_onedrive_falls_back_when_anyone_links_are_not_allowed(monkeypatch):
    calls = []

    def call(client, method, url, **kw):
        calls.append(kw["data"])
        if kw["data"].get("scope") == "anonymous":
            raise cloud.CloudError("not allowed")
        return {"link": {"webUrl": "https://1drv.ms/x"}}
    monkeypatch.setattr(cloud.Client, "call", call)
    assert cloud.OneDrive(_acc("onedrive")).share_link(cloud.Entry("a", False, "id1", "/")) == "https://1drv.ms/x"
    assert calls == [{"type": "view", "scope": "anonymous"}, {"type": "view"}]


def test_dropbox_uses_the_link_it_already_has(monkeypatch):
    rec = Recorder([("list_shared_links", {"links": [{"url": "https://www.dropbox.com/s/old"}]})])
    monkeypatch.setattr(cloud.Client, "call", rec.method)
    assert cloud.Dropbox(_acc("dropbox")).share_link(cloud.Entry("a", False, "/a", "/")) == \
        "https://www.dropbox.com/s/old"
    assert len(rec.asked) == 1  # (no new link made)
    rec = Recorder([("list_shared_links", {"links": []}),
                    ("create_shared_link", {"url": "https://www.dropbox.com/s/new"})])
    monkeypatch.setattr(cloud.Client, "call", rec.method)
    assert cloud.Dropbox(_acc("dropbox")).share_link(cloud.Entry("a", False, "/a", "/")) == \
        "https://www.dropbox.com/s/new"


def test_no_link_for_shared_with_me_or_a_folder_on_this_pc(tmp_path):
    with pytest.raises(cloud.CloudError):
        cloud.GoogleDrive(_acc("gdrive")).share_link(cloud.Entry("Shared with me", True, "sharedWithMe", "/",
                                                                 kind="virtual"))
    local = cloud.client(cloud.Account("folder", "", extra={"local": str(tmp_path)}))
    with pytest.raises(cloud.CloudError):
        local.share_link(cloud.Entry("a", False, str(tmp_path / "a"), "/"))
