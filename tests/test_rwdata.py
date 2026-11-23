"""Retraction Watch updater: download only when GitLab holds a different file."""
import hashlib
import json
import sys
from pathlib import Path

import requests

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

from refcheck import rwdata  # noqa: E402
from refcheck.config import Settings  # noqa: E402
from refcheck.netaudit import NetworkAudit  # noqa: E402
from refcheck.sources import evidence_from_settings  # noqa: E402

HEADER = ("Record ID,Title,Subject,Institution,Journal,Publisher,Country,Author,URLS,ArticleType,"
          "RetractionDate,RetractionDOI,RetractionPubMedID,OriginalPaperDate,OriginalPaperDOI,"
          "OriginalPaperPubMedID,RetractionNature,Reason,Paywalled,Notes\n")


def make_csv(n, extra=""):
    rows = "".join(f'{i},Paper {i},,,J,P,AU,A,,Research Article,1/1/2024 0:00,10.1/r{i},{900 + i},'
                   f'1/1/2020 0:00,10.1/p{i},{100 + i},Retraction,+Error,No,\n' for i in range(1, n + 1))
    return (HEADER + rows + extra).encode()


class Resp:
    def __init__(self, status=200, headers=None, body=b"", fail_midway=False):
        self.status_code, self.headers, self.body, self.fail = status, headers or {}, body, fail_midway

    def iter_content(self, size):
        for i in range(0, len(self.body), 7):
            if self.fail and i > 0:
                raise requests.ConnectionError("reset")
            yield self.body[i:i + 7]


class FakeGitLab:
    """Serves one file. head_status=None means the host is unreachable."""

    def __init__(self, body, head_status=200, give_sha=True, wrong_sha=False, get_status=200,
                 fail_midway=False, etag='"v1"'):
        self.body, self.head_status, self.give_sha, self.wrong_sha = body, head_status, give_sha, wrong_sha
        self.get_status, self.fail_midway, self.etag = get_status, fail_midway, etag
        self.heads, self.gets = 0, []

    def head(self, url, timeout=None, allow_redirects=True):
        assert url == rwdata.META_URL
        self.heads += 1
        if self.head_status is None:
            raise requests.ConnectionError("unreachable")
        sha = hashlib.sha256(self.body).hexdigest()
        h = {"X-Gitlab-Blob-Id": "blob-" + sha[:8], "X-Gitlab-Last-Commit-Id": "c0ffee"}
        if self.give_sha:
            h["X-Gitlab-Content-Sha256"] = ("0" * 64) if self.wrong_sha else sha
        return Resp(self.head_status, h)

    def get(self, url, headers=None, stream=True, timeout=None):
        assert url == rwdata.RAW_URL
        self.gets.append(dict(headers or {}))
        if self.get_status is None:
            raise requests.ConnectionError("unreachable")
        if (headers or {}).get("If-None-Match") == self.etag:
            return Resp(304, {"ETag": self.etag})
        return Resp(self.get_status, {"ETag": self.etag}, self.body, self.fail_midway)


def test_first_run_downloads_then_same_version_is_skipped(tmp_path):
    gl = FakeGitLab(make_csv(50))
    audit = NetworkAudit()
    r = rwdata.ensure_current(str(tmp_path), session=gl, audit=audit)
    assert r.status == "downloaded" and r.rows == 50 and r.usable
    assert Path(r.path).read_bytes() == gl.body
    assert r.sha256 == hashlib.sha256(gl.body).hexdigest() and r.last_commit == "c0ffee"
    r2 = rwdata.ensure_current(str(tmp_path), session=gl, audit=audit)
    assert r2.status == "unchanged" and len(gl.gets) == 1          # second run: checksum only
    assert {x.host for x in audit.requests} == {"gitlab.com"}
    assert all(x.transmitted == {"file": "retraction_watch.csv", "ref": "main"} for x in audit.requests)


def test_new_version_replaces_the_copy(tmp_path):
    rwdata.ensure_current(str(tmp_path), session=FakeGitLab(make_csv(50)))
    gl = FakeGitLab(make_csv(55))
    r = rwdata.ensure_current(str(tmp_path), session=gl)
    assert r.status == "downloaded" and r.rows == 55
    assert Path(r.path).read_bytes() == gl.body


def test_unreachable_or_offline_keeps_the_last_copy(tmp_path):
    first = FakeGitLab(make_csv(50))
    rwdata.ensure_current(str(tmp_path), session=first)
    r = rwdata.ensure_current(str(tmp_path), session=FakeGitLab(b"", head_status=None, get_status=None))
    assert r.status == "stale" and r.usable and "using the local copy downloaded" in r.message
    off = FakeGitLab(make_csv(60))
    r = rwdata.ensure_current(str(tmp_path), offline=True, session=off)
    assert r.status == "offline" and r.usable and off.heads == 0 and not off.gets
    empty = tmp_path / "empty"
    assert rwdata.ensure_current(str(empty), offline=True).status == "unavailable"


def test_bad_downloads_never_replace_the_copy(tmp_path):
    good = make_csv(100)
    rwdata.ensure_current(str(tmp_path), session=FakeGitLab(good))
    csv_path = tmp_path / "retraction_watch.csv"
    cases = [FakeGitLab(make_csv(101), wrong_sha=True),                    # checksum mismatch
             FakeGitLab(b"a,b,c\n1,2,3\n"),                                # not the dataset
             FakeGitLab(make_csv(40)),                                     # far fewer rows
             FakeGitLab(make_csv(101), fail_midway=True),                  # connection dropped
             FakeGitLab(make_csv(101), get_status=503)]
    for gl in cases:
        r = rwdata.ensure_current(str(tmp_path), session=gl)
        assert r.status == "stale" and r.usable, r.message
        assert csv_path.read_bytes() == good
        assert not (tmp_path / "retraction_watch.csv.part").exists()


def test_no_checksum_header_uses_etag_and_identical_bytes(tmp_path):
    body = make_csv(30)
    gl = FakeGitLab(body, give_sha=False)
    assert rwdata.ensure_current(str(tmp_path), session=gl).status == "downloaded"
    # same blob id: skipped without a download
    assert rwdata.ensure_current(str(tmp_path), session=gl).status == "unchanged" and len(gl.gets) == 1
    # metadata unavailable: conditional download answered 304
    gl2 = FakeGitLab(body, head_status=500)
    r = rwdata.ensure_current(str(tmp_path), session=gl2)
    assert r.status == "unchanged" and gl2.gets == [{"If-None-Match": '"v1"'}]
    # metadata gives nothing to compare and the ETag changed: identical bytes are discarded
    st = json.loads((tmp_path / "retraction_watch.state.json").read_text())
    st["blob_id"] = ""
    (tmp_path / "retraction_watch.state.json").write_text(json.dumps(st))
    gl3 = FakeGitLab(body, give_sha=False, etag='"v2"')
    r = rwdata.ensure_current(str(tmp_path), session=gl3)
    assert r.status == "unchanged" and "identical" in r.message


def test_hand_edited_copy_is_refreshed(tmp_path):
    gl = FakeGitLab(make_csv(20))
    rwdata.ensure_current(str(tmp_path), session=gl)
    (tmp_path / "retraction_watch.csv").write_bytes(make_csv(19))
    r = rwdata.ensure_current(str(tmp_path), session=gl)
    assert r.status == "downloaded" and (tmp_path / "retraction_watch.csv").read_bytes() == gl.body


def test_evidence_uses_the_updated_copy_and_reports_its_version(tmp_path):
    s = Settings(cache_path="", sources=["retractionwatch"], retraction_watch_dir=str(tmp_path))
    ev = evidence_from_settings(s, rw_session=FakeGitLab(make_csv(5)))
    assert ev is not None and ev.rw is not None and ev.enabled == set()
    assert ev.rw.lookup("10.1/p3", "")[0]["nature"] == "Retraction"
    pub = ev.rw_status.public()
    assert pub["status"] == "downloaded" and pub["path"] == "retraction_watch.csv"      # no folder names
    assert "5 records" in ev.rw_status.line()
    # a user's own file is used as is: no request at all
    own = tmp_path / "mine.csv"
    own.write_bytes(make_csv(3))
    gl = FakeGitLab(make_csv(9))
    ev = evidence_from_settings(Settings(cache_path="", sources=["retractionwatch"],
                                         retraction_watch_csv=str(own)), rw_session=gl)
    assert ev.rw_status is None and gl.heads == 0 and len(ev.rw.by_doi) == 3
    # source switched off: nothing happens
    gl = FakeGitLab(make_csv(9))
    assert evidence_from_settings(Settings(cache_path="", sources=[], retraction_watch_dir=str(tmp_path)),
                                  rw_session=gl) is None and gl.heads == 0
