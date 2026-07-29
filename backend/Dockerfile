FROM python:3.12-slim
WORKDIR /app
ENV TZ=Asia/Shanghai
# 使用国内 pip 镜像源加速
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
RUN mkdir -p data/qlib_bin/cn_data data/raw models logs
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
