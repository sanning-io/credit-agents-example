# The example as one image. Each service runs from it with its own command:
#
#   underwriter       credit-demo underwriter   (the default command)
#   checker           credit-demo checker
#   evidence-builder  credit-demo evidence-builder
#
# Each command tests that service's settings first, then starts it.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
# The case documents are not in the installed package, so they are copied here
# and CREDIT_DEMO_HOME points at them. The run folders default to runs/ under it.
COPY applications ./applications
COPY policy ./policy
ENV CREDIT_DEMO_HOME=/app

# Every interface, over IPv4 and IPv6: the underwriter's port must be reachable
# from outside, and the underwriter must reach the other two on a private
# network that might carry only IPv6. No service asks for a login, so give the
# checker and the evidence builder no public address.
ENV CREDIT_DEMO_HOST=::

# Each service listens on the PORT the host sets. Without one, the underwriter
# listens on 8001, the checker on 8002 and the evidence builder on 8003.

# Unbuffered, so a line is not lost if the container is killed.
ENV PYTHONUNBUFFERED=1
# Only the underwriter's port: the other two are not for the public.
EXPOSE 8001

CMD ["credit-demo", "underwriter"]
