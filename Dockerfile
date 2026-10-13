# The example in one container, for any host that runs containers. It runs
# the same one command a laptop runs (`credit-demo`, src/credit_demo/dev.py):
# both services as two subprocesses in one container, no orchestration
# package. Running the example on a laptop without a container needs nothing
# in this file.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
# The case documents: each application's folder (the application file and
# the applicant's documents) and the credit policy. The package above is
# installed into site-packages, which holds no documents, so they are copied
# here and CREDIT_DEMO_HOME says where they are
# (src/credit_demo/common/home.py). The run directories default to runs/
# under the same home.
COPY applications ./applications
COPY policy ./policy
ENV CREDIT_DEMO_HOME=/app

# Fixed ports, set here and not read from a $PORT a host injects: two
# services share this container, and each takes its own port from
# UNDERWRITER_PORT or CHECKER_PORT (src/credit_demo/common/config.py). The
# underwriter reaches the checker over CHECKER_URL. Point the host's public
# address at 8001, the underwriter's port.
ENV UNDERWRITER_PORT=8001
ENV CHECKER_PORT=8002
ENV CHECKER_URL=http://localhost:8002
# The checker agent asks the credit desk for each case's documents over
# DESK_URL, the underwriter's own port inside this same container.
ENV DESK_URL=http://localhost:8001
# Every interface, inside the container, for the UNDERWRITER only: it is the
# one service whose port the host's network has to reach, and only 8001 is
# exposed. The checker binds to loopback always (`bind_host` in
# src/credit_demo/common/config.py), this variable included: the underwriter
# reaches it over CHECKER_URL, http://localhost:8002, inside this same
# container, and nothing outside the container reaches the checker's own
# unauthenticated endpoints.
ENV CREDIT_DEMO_HOST=0.0.0.0
# Every line a service prints reaches the container log when it is printed.
# Standard output is block-buffered when it is not a terminal, and a line
# still in the buffer is lost if the container is killed.
ENV PYTHONUNBUFFERED=1
EXPOSE 8001

CMD ["credit-demo"]
