# Engine + web app for Cloud Run.
#
# The staged region is baked into the image rather than read from Cloud Storage. For a live demo
# that is the right trade: the data is 39 MB and never changes between deploys, so shipping it
# removes a network dependency from the request path entirely. `Region` still accepts a gs:// root
# if that changes later.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Numba writes its JIT cache here; the image filesystem is read-only at runtime.
    NUMBA_CACHE_DIR=/tmp/numba \
    # Keep GDAL from listing directories it does not need to.
    GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR

# rasterio and pyogrio ship their own GDAL in the wheel, but it still links against a few system
# libraries that python:*-slim leaves out. libexpat is the one GDAL needs for its XML drivers.
RUN apt-get update  && apt-get install -y --no-install-recommends libexpat1  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY service/requirements.txt ./service/requirements.txt
RUN pip install --no-cache-dir -r service/requirements.txt

COPY engine/bih_engine ./engine/bih_engine
COPY engine/bih_agents ./engine/bih_agents
COPY service ./service
COPY web ./web
COPY data/regions ./data/regions
COPY replay ./replay

# Warm the numba cache at build time so the first drawn box is not the one that pays for it.
RUN python -c "import sys; sys.path.insert(0, 'engine'); \
import numpy as np; from bih_engine.hydrology import fill_and_route; \
d = np.zeros((32, 32)); o = np.zeros((32, 32), bool); o[0]=o[-1]=True; o[:,0]=o[:,-1]=True; \
fill_and_route(d, o); print('numba warm')"

EXPOSE 8080
CMD exec uvicorn app:app --app-dir service --host 0.0.0.0 --port ${PORT:-8080}
