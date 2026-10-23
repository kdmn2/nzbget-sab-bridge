# Third-party notices

The original bridge source in this repository is licensed under the MIT License
in [LICENSE](LICENSE).

The runtime container includes the following Python packages. Their upstream
license texts are included in [licenses/](licenses/). Versions match the pinned
runtime requirements in [requirements.txt](requirements.txt).

| Package | Version | License | Upstream |
|---|---:|---|---|
| annotated-doc | 0.0.5 | MIT | https://pypi.org/project/annotated-doc/ |
| annotated-types | 0.8.0 | MIT | https://pypi.org/project/annotated-types/ |
| anyio | 4.15.1 | MIT | https://github.com/agronholm/anyio |
| certifi | 2026.10.19 | MPL-2.0 | https://github.com/certifi/python-certifi |
| click | 8.5.0 | BSD-3-Clause | https://github.com/pallets/click |
| fastapi | 0.161.1 | MIT | https://github.com/fastapi/fastapi |
| h11 | 0.16.0 | MIT | https://github.com/python-hyper/h11 |
| httpcore | 1.0.9 | BSD-3-Clause | https://github.com/encode/httpcore |
| httpx | 0.28.1 | BSD-3-Clause | https://github.com/encode/httpx |
| idna | 3.20 | BSD-3-Clause | https://github.com/kjd/idna |
| opentelemetry-api | 1.45.1 | Apache-2.0 | https://github.com/open-telemetry/opentelemetry-python |
| pydantic | 2.14.1 | MIT | https://github.com/pydantic/pydantic |
| pydantic-core | 2.50.1 | MIT | https://github.com/pydantic/pydantic |
| starlette | 1.9.0 | BSD-3-Clause | https://github.com/encode/starlette |
| typing-inspection | 0.4.4 | MIT | https://pypi.org/project/typing-inspection/ |
| typing-extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| uvicorn | 0.54.0 | BSD-3-Clause | https://github.com/encode/uvicorn |

The base image also contains Python and pip under their respective upstream
licenses. The image is built from the digest-pinned Python base image referenced
in [Dockerfile](Dockerfile).

NZBGet, SABnzbd, NZBClient, Unraid, and other product names are trademarks of
their respective owners. This project is an independent compatibility bridge
and is not affiliated with or endorsed by those projects.
