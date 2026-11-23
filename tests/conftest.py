"""Keep the test suite offline and independent of the person's environment.

Secondary sources are off unless a test enables them with a mocked service. Every setting
that a user may export for real runs (NCBI_EMAIL, NCBI_API_KEY, any REFCHECK_* variable,
for example REFCHECK_SCORING or REFCHECK_WEIGHTS) is removed first: with NCBI_EMAIL set,
the validation app would otherwise start a real live run from a test."""
import os

for _name in list(os.environ):
    if _name.startswith("REFCHECK_") or _name in ("NCBI_EMAIL", "NCBI_API_KEY", "XDG_CACHE_HOME_REFCHECK"):
        os.environ.pop(_name, None)
os.environ["REFCHECK_SOURCES"] = "none"
