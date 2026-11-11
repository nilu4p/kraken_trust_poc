# Optional xView3 second-opinion service (docker compose --profile ai).
# Information only: KRAKEN displays its percentages, never decides with them.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp \
    XVIEW3_WEIGHTS=/models/b4_fold0.pth

WORKDIR /srv
RUN pip install --no-cache-dir torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir --no-deps \
      "pytorch-toolbelt @ https://codeload.github.com/BloodAxe/pytorch-toolbelt/zip/e52fbed6cca16151423c4ae396256308696dfc8f" \
 && ! pip show opencv-python >/dev/null 2>&1
COPY vendor/ vendor/
COPY app.py .
# Fail the build, not the service: the whole model must be constructible (weights excluded).
RUN python -c "import sys; sys.path.insert(0, 'vendor'); import cv2, app; \
from pytorch_toolbelt.modules.encoders import TimmB4Encoder; \
from xview3.centernet.models import CenterNetUNetModel, DecoupledHeadGroupNormLateShuffle; print('xView3 imports OK, OpenCV', cv2.__version__)"

RUN useradd --system --uid 10001 xview3 && mkdir -p /models && chown xview3 /models
USER xview3
EXPOSE 8091
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8091/health', timeout=4)"
CMD ["python", "app.py"]
