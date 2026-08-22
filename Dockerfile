FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY ortools-9.15.6755-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl /tmp/ortools-9.15.6755-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir --index-url ${PIP_INDEX_URL} /tmp/ortools-9.15.6755-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl && pip install --no-cache-dir --index-url ${PIP_INDEX_URL} . && useradd --create-home --uid 10001 supplymind
COPY --chown=supplymind:supplymind app ./app
COPY --chown=supplymind:supplymind sample_data ./sample_data
USER supplymind
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
