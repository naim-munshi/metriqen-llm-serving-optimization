# Secure Mac Python dependency setup and PyPI certificate diagnosis

**Observed October 21, 2026:** `python3` on the operator Mac is Homebrew
Python 3.14.5; the `.venv` was created correctly. `pip install -e
"./services/control-api[dev]"` failed while accessing
`https://pypi.org/simple/setuptools/` because the presented TLS
certificate was **not trusted**. Since the installation did not complete,
`python3 -m pytest` failed with `No module named pytest`. That is a local
network/certificate and dependency-setup blocker—not evidence of an
application-level unit test regression.

The repository CI explicitly runs Python **3.13** and currently passes its
hosted source tests. The package declares `requires-python >=3.12`, but
hosted success on 3.13 does **not** establish 3.14 compatibility.

## Diagnose without disabling HTTPS verification

Run from the repository with an activated virtual environment:

```sh
cd ~/metriqen-llm-serving-optimization
. .venv/bin/activate
python3 --version
python3 -m pip --version
python3 -c 'import ssl; print(ssl.get_default_verify_paths())'
curl -fsSI --max-time 15 https://pypi.org/simple/setuptools/
```

These checks do not install software and should not print secrets. Do not
paste raw proxy URLs, credentials, private certificate files, or the complete
output of `pip config debug` into issues/PRs. If further network inspection
is required, share only redacted error text and whether a proxy/VPN/managed
network is used.

- **If both curl and pip reject TLS:** confirm the network time and trusted
  certificate chain. If a university/corporate network inspects HTTPS,
  validate its root CA out-of-band with the legitimate network administrator.
  Use a different **trusted** network if appropriate. Never install an
  unknown CA or blindly mark a certificate trusted because a page suggests it.
- **If curl succeeds but pip rejects TLS:** compare Python/pip's trust store
  with the system trust store. Modern pip normally uses the system store and
  `certifi`. One strictly diagnostic attempt with
  `python3 -m pip --use-deprecated=legacy-certs index versions setuptools`
  uses the certifi-only store **without disabling validation**. This may
  help distinguish system-certificate problems; it is not a substitute for
  determining why a certificate is untrusted.
- **If a verified organization CA bundle is required:** use pip's documented
  `--cert /path/to/verified-ca-bundle.pem` or `PIP_CERT` option, only after
  verifying the issuing authority independently. Never commit or paste a
  private CA bundle or secrets.
- **Never use `--trusted-host`, a disabled TLS verifier, or an insecure
  package mirror** to get the portfolio tests green.

## Retry after trusted HTTPS works

```sh
cd ~/metriqen-llm-serving-optimization
. .venv/bin/activate
python3 -m pip install --no-cache-dir -e "./services/control-api[dev]" \
  && make portfolio-ci
```

If packages are unavailable specifically for Python 3.14 even after TLS
works, select a tested Python **3.13** interpreter already installed (or
install it over a verified secure channel), then create a **separate** venv:

```sh
python3.13 -m venv .venv313
. .venv313/bin/activate
python3 -m pip install -e "./services/control-api[dev]" \
  && make portfolio-ci
```

Check that `.venv313/` is ignored before creating it. No inference
benchmark, GPU proof, real-browser inspection, live PostgreSQL/Redis, or
operator recovery test is implied by hosted or local source tests alone.

Primary technical reference:
[pip HTTPS certificates](https://pip.pypa.io/en/stable/topics/https-certificates/).
