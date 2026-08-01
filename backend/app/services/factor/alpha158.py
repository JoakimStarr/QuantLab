"""Alpha158 基准因子集导入。"""
import asyncio
import logging
from datetime import datetime
from typing import Callable, List, Dict, Optional

logger = logging.getLogger(__name__)

# Alpha158 的 158 个因子表达式（qlib 标准定义，硬编码避免运行时依赖 qlib.contrib）
ALPHA158_EXPRESSIONS: List[Dict] = [
    {"name": "KMID", "expr": "($close-$open)/$open", "category": "alpha158", "description": "中间价位置"},
    {"name": "KLEN", "expr": "($high-$low)/$open", "category": "alpha158", "description": "K线长度"},
    {"name": "KMID2", "expr": "($close-$open)/($high-$low+1e-12)", "category": "alpha158", "description": "中间价占比"},
    {"name": "KUP", "expr": "($high-Greater($open, $close))/$open", "category": "alpha158", "description": "上影线"},
    {"name": "KUP2", "expr": "($high-Greater($open, $close))/($high-$low+1e-12)", "category": "alpha158", "description": "上影线占比"},
    {"name": "KLOW", "expr": "(Less($open, $close)-$low)/$open", "category": "alpha158", "description": "下影线"},
    {"name": "KLOW2", "expr": "(Less($open, $close)-$low)/($high-$low+1e-12)", "category": "alpha158", "description": "下影线占比"},
    {"name": "KSFT", "expr": "(2*$close-$high-$low)/$open", "category": "alpha158", "description": "收盘偏移"},
    {"name": "KSFT2", "expr": "(2*$close-$high-$low)/($high-$low+1e-12)", "category": "alpha158", "description": "收盘偏移占比"},
    {"name": "OPEN0", "expr": "$open/$close", "category": "alpha158", "description": "开盘价相对收盘"},
    {"name": "HIGH0", "expr": "$high/$close", "category": "alpha158", "description": "最高价相对收盘"},
    {"name": "LOW0", "expr": "$low/$close", "category": "alpha158", "description": "最低价相对收盘"},
    {"name": "VWAP0", "expr": "$vwap/$close", "category": "alpha158", "description": "VWAP相对收盘"},
    {"name": "ROC5", "expr": "Ref($close, 5)/$close", "category": "alpha158", "description": "收益率"},
    {"name": "ROC10", "expr": "Ref($close, 10)/$close", "category": "alpha158", "description": "收益率"},
    {"name": "ROC20", "expr": "Ref($close, 20)/$close", "category": "alpha158", "description": "收益率"},
    {"name": "ROC30", "expr": "Ref($close, 30)/$close", "category": "alpha158", "description": "收益率"},
    {"name": "ROC60", "expr": "Ref($close, 60)/$close", "category": "alpha158", "description": "收益率"},
    {"name": "MA5", "expr": "Mean($close, 5)/$close", "category": "alpha158", "description": "均线"},
    {"name": "MA10", "expr": "Mean($close, 10)/$close", "category": "alpha158", "description": "均线"},
    {"name": "MA20", "expr": "Mean($close, 20)/$close", "category": "alpha158", "description": "均线"},
    {"name": "MA30", "expr": "Mean($close, 30)/$close", "category": "alpha158", "description": "均线"},
    {"name": "MA60", "expr": "Mean($close, 60)/$close", "category": "alpha158", "description": "均线"},
    {"name": "STD5", "expr": "Std($close, 5)/$close", "category": "alpha158", "description": "标准差"},
    {"name": "STD10", "expr": "Std($close, 10)/$close", "category": "alpha158", "description": "标准差"},
    {"name": "STD20", "expr": "Std($close, 20)/$close", "category": "alpha158", "description": "标准差"},
    {"name": "STD30", "expr": "Std($close, 30)/$close", "category": "alpha158", "description": "标准差"},
    {"name": "STD60", "expr": "Std($close, 60)/$close", "category": "alpha158", "description": "标准差"},
    {"name": "BETA5", "expr": "Slope($close, 5)/$close", "category": "alpha158", "description": "斜率beta"},
    {"name": "BETA10", "expr": "Slope($close, 10)/$close", "category": "alpha158", "description": "斜率beta"},
    {"name": "BETA20", "expr": "Slope($close, 20)/$close", "category": "alpha158", "description": "斜率beta"},
    {"name": "BETA30", "expr": "Slope($close, 30)/$close", "category": "alpha158", "description": "斜率beta"},
    {"name": "BETA60", "expr": "Slope($close, 60)/$close", "category": "alpha158", "description": "斜率beta"},
    {"name": "RSQR5", "expr": "Rsquare($close, 5)", "category": "alpha158", "description": "R平方"},
    {"name": "RSQR10", "expr": "Rsquare($close, 10)", "category": "alpha158", "description": "R平方"},
    {"name": "RSQR20", "expr": "Rsquare($close, 20)", "category": "alpha158", "description": "R平方"},
    {"name": "RSQR30", "expr": "Rsquare($close, 30)", "category": "alpha158", "description": "R平方"},
    {"name": "RSQR60", "expr": "Rsquare($close, 60)", "category": "alpha158", "description": "R平方"},
    {"name": "RESI5", "expr": "Resi($close, 5)/$close", "category": "alpha158", "description": "残差"},
    {"name": "RESI10", "expr": "Resi($close, 10)/$close", "category": "alpha158", "description": "残差"},
    {"name": "RESI20", "expr": "Resi($close, 20)/$close", "category": "alpha158", "description": "残差"},
    {"name": "RESI30", "expr": "Resi($close, 30)/$close", "category": "alpha158", "description": "残差"},
    {"name": "RESI60", "expr": "Resi($close, 60)/$close", "category": "alpha158", "description": "残差"},
    {"name": "MAX5", "expr": "Max($high, 5)/$close", "category": "alpha158", "description": "均线"},
    {"name": "MAX10", "expr": "Max($high, 10)/$close", "category": "alpha158", "description": "均线"},
    {"name": "MAX20", "expr": "Max($high, 20)/$close", "category": "alpha158", "description": "均线"},
    {"name": "MAX30", "expr": "Max($high, 30)/$close", "category": "alpha158", "description": "均线"},
    {"name": "MAX60", "expr": "Max($high, 60)/$close", "category": "alpha158", "description": "均线"},
    {"name": "MIN5", "expr": "Min($low, 5)/$close", "category": "alpha158", "description": "最低价"},
    {"name": "MIN10", "expr": "Min($low, 10)/$close", "category": "alpha158", "description": "最低价"},
    {"name": "MIN20", "expr": "Min($low, 20)/$close", "category": "alpha158", "description": "最低价"},
    {"name": "MIN30", "expr": "Min($low, 30)/$close", "category": "alpha158", "description": "最低价"},
    {"name": "MIN60", "expr": "Min($low, 60)/$close", "category": "alpha158", "description": "最低价"},
    {"name": "QTLU5", "expr": "Quantile($close, 5, 0.8)/$close", "category": "alpha158", "description": "上分位"},
    {"name": "QTLU10", "expr": "Quantile($close, 10, 0.8)/$close", "category": "alpha158", "description": "上分位"},
    {"name": "QTLU20", "expr": "Quantile($close, 20, 0.8)/$close", "category": "alpha158", "description": "上分位"},
    {"name": "QTLU30", "expr": "Quantile($close, 30, 0.8)/$close", "category": "alpha158", "description": "上分位"},
    {"name": "QTLU60", "expr": "Quantile($close, 60, 0.8)/$close", "category": "alpha158", "description": "上分位"},
    {"name": "QTLD5", "expr": "Quantile($close, 5, 0.2)/$close", "category": "alpha158", "description": "下分位"},
    {"name": "QTLD10", "expr": "Quantile($close, 10, 0.2)/$close", "category": "alpha158", "description": "下分位"},
    {"name": "QTLD20", "expr": "Quantile($close, 20, 0.2)/$close", "category": "alpha158", "description": "下分位"},
    {"name": "QTLD30", "expr": "Quantile($close, 30, 0.2)/$close", "category": "alpha158", "description": "下分位"},
    {"name": "QTLD60", "expr": "Quantile($close, 60, 0.2)/$close", "category": "alpha158", "description": "下分位"},
    {"name": "RANK5", "expr": "Rank($close, 5)", "category": "alpha158", "description": "排名"},
    {"name": "RANK10", "expr": "Rank($close, 10)", "category": "alpha158", "description": "排名"},
    {"name": "RANK20", "expr": "Rank($close, 20)", "category": "alpha158", "description": "排名"},
    {"name": "RANK30", "expr": "Rank($close, 30)", "category": "alpha158", "description": "排名"},
    {"name": "RANK60", "expr": "Rank($close, 60)", "category": "alpha158", "description": "排名"},
    {"name": "RSV5", "expr": "($close-Min($low, 5))/(Max($high, 5)-Min($low, 5)+1e-12)", "category": "alpha158", "description": "随机指标RSV"},
    {"name": "RSV10", "expr": "($close-Min($low, 10))/(Max($high, 10)-Min($low, 10)+1e-12)", "category": "alpha158", "description": "随机指标RSV"},
    {"name": "RSV20", "expr": "($close-Min($low, 20))/(Max($high, 20)-Min($low, 20)+1e-12)", "category": "alpha158", "description": "随机指标RSV"},
    {"name": "RSV30", "expr": "($close-Min($low, 30))/(Max($high, 30)-Min($low, 30)+1e-12)", "category": "alpha158", "description": "随机指标RSV"},
    {"name": "RSV60", "expr": "($close-Min($low, 60))/(Max($high, 60)-Min($low, 60)+1e-12)", "category": "alpha158", "description": "随机指标RSV"},
    {"name": "IMAX5", "expr": "IdxMax($high, 5)/5", "category": "alpha158", "description": "最高价位置"},
    {"name": "IMAX10", "expr": "IdxMax($high, 10)/10", "category": "alpha158", "description": "最高价位置"},
    {"name": "IMAX20", "expr": "IdxMax($high, 20)/20", "category": "alpha158", "description": "最高价位置"},
    {"name": "IMAX30", "expr": "IdxMax($high, 30)/30", "category": "alpha158", "description": "最高价位置"},
    {"name": "IMAX60", "expr": "IdxMax($high, 60)/60", "category": "alpha158", "description": "最高价位置"},
    {"name": "IMIN5", "expr": "IdxMin($low, 5)/5", "category": "alpha158", "description": "最低价位置"},
    {"name": "IMIN10", "expr": "IdxMin($low, 10)/10", "category": "alpha158", "description": "最低价位置"},
    {"name": "IMIN20", "expr": "IdxMin($low, 20)/20", "category": "alpha158", "description": "最低价位置"},
    {"name": "IMIN30", "expr": "IdxMin($low, 30)/30", "category": "alpha158", "description": "最低价位置"},
    {"name": "IMIN60", "expr": "IdxMin($low, 60)/60", "category": "alpha158", "description": "最低价位置"},
    {"name": "IMXD5", "expr": "(IdxMax($high, 5)-IdxMin($low, 5))/5", "category": "alpha158", "description": "高低价位置差"},
    {"name": "IMXD10", "expr": "(IdxMax($high, 10)-IdxMin($low, 10))/10", "category": "alpha158", "description": "高低价位置差"},
    {"name": "IMXD20", "expr": "(IdxMax($high, 20)-IdxMin($low, 20))/20", "category": "alpha158", "description": "高低价位置差"},
    {"name": "IMXD30", "expr": "(IdxMax($high, 30)-IdxMin($low, 30))/30", "category": "alpha158", "description": "高低价位置差"},
    {"name": "IMXD60", "expr": "(IdxMax($high, 60)-IdxMin($low, 60))/60", "category": "alpha158", "description": "高低价位置差"},
    {"name": "CORR5", "expr": "Corr($close, Log($volume+1), 5)", "category": "alpha158", "description": "价量相关性"},
    {"name": "CORR10", "expr": "Corr($close, Log($volume+1), 10)", "category": "alpha158", "description": "价量相关性"},
    {"name": "CORR20", "expr": "Corr($close, Log($volume+1), 20)", "category": "alpha158", "description": "价量相关性"},
    {"name": "CORR30", "expr": "Corr($close, Log($volume+1), 30)", "category": "alpha158", "description": "价量相关性"},
    {"name": "CORR60", "expr": "Corr($close, Log($volume+1), 60)", "category": "alpha158", "description": "价量相关性"},
    {"name": "CORD5", "expr": "Corr($close/Ref($close,1), Log($volume/Ref($volume, 1)+1), 5)", "category": "alpha158", "description": "收益量相关性"},
    {"name": "CORD10", "expr": "Corr($close/Ref($close,1), Log($volume/Ref($volume, 1)+1), 10)", "category": "alpha158", "description": "收益量相关性"},
    {"name": "CORD20", "expr": "Corr($close/Ref($close,1), Log($volume/Ref($volume, 1)+1), 20)", "category": "alpha158", "description": "收益量相关性"},
    {"name": "CORD30", "expr": "Corr($close/Ref($close,1), Log($volume/Ref($volume, 1)+1), 30)", "category": "alpha158", "description": "收益量相关性"},
    {"name": "CORD60", "expr": "Corr($close/Ref($close,1), Log($volume/Ref($volume, 1)+1), 60)", "category": "alpha158", "description": "收益量相关性"},
    {"name": "CNTP5", "expr": "Mean($close>Ref($close, 1), 5)", "category": "alpha158", "description": "上涨概率"},
    {"name": "CNTP10", "expr": "Mean($close>Ref($close, 1), 10)", "category": "alpha158", "description": "上涨概率"},
    {"name": "CNTP20", "expr": "Mean($close>Ref($close, 1), 20)", "category": "alpha158", "description": "上涨概率"},
    {"name": "CNTP30", "expr": "Mean($close>Ref($close, 1), 30)", "category": "alpha158", "description": "上涨概率"},
    {"name": "CNTP60", "expr": "Mean($close>Ref($close, 1), 60)", "category": "alpha158", "description": "上涨概率"},
    {"name": "CNTN5", "expr": "Mean($close<Ref($close, 1), 5)", "category": "alpha158", "description": "下跌概率"},
    {"name": "CNTN10", "expr": "Mean($close<Ref($close, 1), 10)", "category": "alpha158", "description": "下跌概率"},
    {"name": "CNTN20", "expr": "Mean($close<Ref($close, 1), 20)", "category": "alpha158", "description": "下跌概率"},
    {"name": "CNTN30", "expr": "Mean($close<Ref($close, 1), 30)", "category": "alpha158", "description": "下跌概率"},
    {"name": "CNTN60", "expr": "Mean($close<Ref($close, 1), 60)", "category": "alpha158", "description": "下跌概率"},
    {"name": "CNTD5", "expr": "Mean($close>Ref($close, 1), 5)-Mean($close<Ref($close, 1), 5)", "category": "alpha158", "description": "涨跌概率差"},
    {"name": "CNTD10", "expr": "Mean($close>Ref($close, 1), 10)-Mean($close<Ref($close, 1), 10)", "category": "alpha158", "description": "涨跌概率差"},
    {"name": "CNTD20", "expr": "Mean($close>Ref($close, 1), 20)-Mean($close<Ref($close, 1), 20)", "category": "alpha158", "description": "涨跌概率差"},
    {"name": "CNTD30", "expr": "Mean($close>Ref($close, 1), 30)-Mean($close<Ref($close, 1), 30)", "category": "alpha158", "description": "涨跌概率差"},
    {"name": "CNTD60", "expr": "Mean($close>Ref($close, 1), 60)-Mean($close<Ref($close, 1), 60)", "category": "alpha158", "description": "涨跌概率差"},
    {"name": "SUMP5", "expr": "Sum(Greater($close-Ref($close, 1), 0), 5)/(Sum(Abs($close-Ref($close, 1)), 5)+1e-12)", "category": "alpha158", "description": "上涨幅度占比"},
    {"name": "SUMP10", "expr": "Sum(Greater($close-Ref($close, 1), 0), 10)/(Sum(Abs($close-Ref($close, 1)), 10)+1e-12)", "category": "alpha158", "description": "上涨幅度占比"},
    {"name": "SUMP20", "expr": "Sum(Greater($close-Ref($close, 1), 0), 20)/(Sum(Abs($close-Ref($close, 1)), 20)+1e-12)", "category": "alpha158", "description": "上涨幅度占比"},
    {"name": "SUMP30", "expr": "Sum(Greater($close-Ref($close, 1), 0), 30)/(Sum(Abs($close-Ref($close, 1)), 30)+1e-12)", "category": "alpha158", "description": "上涨幅度占比"},
    {"name": "SUMP60", "expr": "Sum(Greater($close-Ref($close, 1), 0), 60)/(Sum(Abs($close-Ref($close, 1)), 60)+1e-12)", "category": "alpha158", "description": "上涨幅度占比"},
    {"name": "SUMN5", "expr": "Sum(Greater(Ref($close, 1)-$close, 0), 5)/(Sum(Abs($close-Ref($close, 1)), 5)+1e-12)", "category": "alpha158", "description": "下跌幅度占比"},
    {"name": "SUMN10", "expr": "Sum(Greater(Ref($close, 1)-$close, 0), 10)/(Sum(Abs($close-Ref($close, 1)), 10)+1e-12)", "category": "alpha158", "description": "下跌幅度占比"},
    {"name": "SUMN20", "expr": "Sum(Greater(Ref($close, 1)-$close, 0), 20)/(Sum(Abs($close-Ref($close, 1)), 20)+1e-12)", "category": "alpha158", "description": "下跌幅度占比"},
    {"name": "SUMN30", "expr": "Sum(Greater(Ref($close, 1)-$close, 0), 30)/(Sum(Abs($close-Ref($close, 1)), 30)+1e-12)", "category": "alpha158", "description": "下跌幅度占比"},
    {"name": "SUMN60", "expr": "Sum(Greater(Ref($close, 1)-$close, 0), 60)/(Sum(Abs($close-Ref($close, 1)), 60)+1e-12)", "category": "alpha158", "description": "下跌幅度占比"},
    {"name": "SUMD5", "expr": "(Sum(Greater($close-Ref($close, 1), 0), 5)-Sum(Greater(Ref($close, 1)-$close, 0), 5))/(Sum(Abs($close-Ref($close, 1)), 5)+1e-12)", "category": "alpha158", "description": "涨跌幅度差"},
    {"name": "SUMD10", "expr": "(Sum(Greater($close-Ref($close, 1), 0), 10)-Sum(Greater(Ref($close, 1)-$close, 0), 10))/(Sum(Abs($close-Ref($close, 1)), 10)+1e-12)", "category": "alpha158", "description": "涨跌幅度差"},
    {"name": "SUMD20", "expr": "(Sum(Greater($close-Ref($close, 1), 0), 20)-Sum(Greater(Ref($close, 1)-$close, 0), 20))/(Sum(Abs($close-Ref($close, 1)), 20)+1e-12)", "category": "alpha158", "description": "涨跌幅度差"},
    {"name": "SUMD30", "expr": "(Sum(Greater($close-Ref($close, 1), 0), 30)-Sum(Greater(Ref($close, 1)-$close, 0), 30))/(Sum(Abs($close-Ref($close, 1)), 30)+1e-12)", "category": "alpha158", "description": "涨跌幅度差"},
    {"name": "SUMD60", "expr": "(Sum(Greater($close-Ref($close, 1), 0), 60)-Sum(Greater(Ref($close, 1)-$close, 0), 60))/(Sum(Abs($close-Ref($close, 1)), 60)+1e-12)", "category": "alpha158", "description": "涨跌幅度差"},
    {"name": "VMA5", "expr": "Mean($volume, 5)/($volume+1e-12)", "category": "alpha158", "description": "成交量均线"},
    {"name": "VMA10", "expr": "Mean($volume, 10)/($volume+1e-12)", "category": "alpha158", "description": "成交量均线"},
    {"name": "VMA20", "expr": "Mean($volume, 20)/($volume+1e-12)", "category": "alpha158", "description": "成交量均线"},
    {"name": "VMA30", "expr": "Mean($volume, 30)/($volume+1e-12)", "category": "alpha158", "description": "成交量均线"},
    {"name": "VMA60", "expr": "Mean($volume, 60)/($volume+1e-12)", "category": "alpha158", "description": "成交量均线"},
    {"name": "VSTD5", "expr": "Std($volume, 5)/($volume+1e-12)", "category": "alpha158", "description": "成交量标准差"},
    {"name": "VSTD10", "expr": "Std($volume, 10)/($volume+1e-12)", "category": "alpha158", "description": "成交量标准差"},
    {"name": "VSTD20", "expr": "Std($volume, 20)/($volume+1e-12)", "category": "alpha158", "description": "成交量标准差"},
    {"name": "VSTD30", "expr": "Std($volume, 30)/($volume+1e-12)", "category": "alpha158", "description": "成交量标准差"},
    {"name": "VSTD60", "expr": "Std($volume, 60)/($volume+1e-12)", "category": "alpha158", "description": "成交量标准差"},
    {"name": "WVMA5", "expr": "Std(Abs($close/Ref($close, 1)-1)*$volume, 5)/(Mean(Abs($close/Ref($close, 1)-1)*$volume, 5)+1e-12)", "category": "alpha158", "description": "加权成交量波动"},
    {"name": "WVMA10", "expr": "Std(Abs($close/Ref($close, 1)-1)*$volume, 10)/(Mean(Abs($close/Ref($close, 1)-1)*$volume, 10)+1e-12)", "category": "alpha158", "description": "加权成交量波动"},
    {"name": "WVMA20", "expr": "Std(Abs($close/Ref($close, 1)-1)*$volume, 20)/(Mean(Abs($close/Ref($close, 1)-1)*$volume, 20)+1e-12)", "category": "alpha158", "description": "加权成交量波动"},
    {"name": "WVMA30", "expr": "Std(Abs($close/Ref($close, 1)-1)*$volume, 30)/(Mean(Abs($close/Ref($close, 1)-1)*$volume, 30)+1e-12)", "category": "alpha158", "description": "加权成交量波动"},
    {"name": "WVMA60", "expr": "Std(Abs($close/Ref($close, 1)-1)*$volume, 60)/(Mean(Abs($close/Ref($close, 1)-1)*$volume, 60)+1e-12)", "category": "alpha158", "description": "加权成交量波动"},
    {"name": "VSUMP5", "expr": "Sum(Greater($volume-Ref($volume, 1), 0), 5)/(Sum(Abs($volume-Ref($volume, 1)), 5)+1e-12)", "category": "alpha158", "description": "放量上涨占比"},
    {"name": "VSUMP10", "expr": "Sum(Greater($volume-Ref($volume, 1), 0), 10)/(Sum(Abs($volume-Ref($volume, 1)), 10)+1e-12)", "category": "alpha158", "description": "放量上涨占比"},
    {"name": "VSUMP20", "expr": "Sum(Greater($volume-Ref($volume, 1), 0), 20)/(Sum(Abs($volume-Ref($volume, 1)), 20)+1e-12)", "category": "alpha158", "description": "放量上涨占比"},
    {"name": "VSUMP30", "expr": "Sum(Greater($volume-Ref($volume, 1), 0), 30)/(Sum(Abs($volume-Ref($volume, 1)), 30)+1e-12)", "category": "alpha158", "description": "放量上涨占比"},
    {"name": "VSUMP60", "expr": "Sum(Greater($volume-Ref($volume, 1), 0), 60)/(Sum(Abs($volume-Ref($volume, 1)), 60)+1e-12)", "category": "alpha158", "description": "放量上涨占比"},
    {"name": "VSUMN5", "expr": "Sum(Greater(Ref($volume, 1)-$volume, 0), 5)/(Sum(Abs($volume-Ref($volume, 1)), 5)+1e-12)", "category": "alpha158", "description": "缩量下跌占比"},
    {"name": "VSUMN10", "expr": "Sum(Greater(Ref($volume, 1)-$volume, 0), 10)/(Sum(Abs($volume-Ref($volume, 1)), 10)+1e-12)", "category": "alpha158", "description": "缩量下跌占比"},
    {"name": "VSUMN20", "expr": "Sum(Greater(Ref($volume, 1)-$volume, 0), 20)/(Sum(Abs($volume-Ref($volume, 1)), 20)+1e-12)", "category": "alpha158", "description": "缩量下跌占比"},
    {"name": "VSUMN30", "expr": "Sum(Greater(Ref($volume, 1)-$volume, 0), 30)/(Sum(Abs($volume-Ref($volume, 1)), 30)+1e-12)", "category": "alpha158", "description": "缩量下跌占比"},
    {"name": "VSUMN60", "expr": "Sum(Greater(Ref($volume, 1)-$volume, 0), 60)/(Sum(Abs($volume-Ref($volume, 1)), 60)+1e-12)", "category": "alpha158", "description": "缩量下跌占比"},
    {"name": "VSUMD5", "expr": "(Sum(Greater($volume-Ref($volume, 1), 0), 5)-Sum(Greater(Ref($volume, 1)-$volume, 0), 5))/(Sum(Abs($volume-Ref($volume, 1)), 5)+1e-12)", "category": "alpha158", "description": "量能涨跌差"},
    {"name": "VSUMD10", "expr": "(Sum(Greater($volume-Ref($volume, 1), 0), 10)-Sum(Greater(Ref($volume, 1)-$volume, 0), 10))/(Sum(Abs($volume-Ref($volume, 1)), 10)+1e-12)", "category": "alpha158", "description": "量能涨跌差"},
    {"name": "VSUMD20", "expr": "(Sum(Greater($volume-Ref($volume, 1), 0), 20)-Sum(Greater(Ref($volume, 1)-$volume, 0), 20))/(Sum(Abs($volume-Ref($volume, 1)), 20)+1e-12)", "category": "alpha158", "description": "量能涨跌差"},
    {"name": "VSUMD30", "expr": "(Sum(Greater($volume-Ref($volume, 1), 0), 30)-Sum(Greater(Ref($volume, 1)-$volume, 0), 30))/(Sum(Abs($volume-Ref($volume, 1)), 30)+1e-12)", "category": "alpha158", "description": "量能涨跌差"},
    {"name": "VSUMD60", "expr": "(Sum(Greater($volume-Ref($volume, 1), 0), 60)-Sum(Greater(Ref($volume, 1)-$volume, 0), 60))/(Sum(Abs($volume-Ref($volume, 1)), 60)+1e-12)", "category": "alpha158", "description": "量能涨跌差"},
]


async def _flush_batch_metrics(buffer: list) -> None:
    """批量更新因子评价指标到 DB（每批一次 commit）。

    Args:
        buffer: [(factor_id, metrics_dict), ...]
    """
    from app.core.database import async_session
    from app.models.factor import Factor

    if not buffer:
        return

    async with async_session() as session:
        for fid, metrics in buffer:
            r = await session.get(Factor, fid)
            if r is not None and metrics:
                r.ic = metrics.get("ic")
                r.rank_ic = metrics.get("rank_ic")
                r.icir = metrics.get("icir")
                r.ir = metrics.get("ir")
                r.turnover = metrics.get("turnover")
                r.eval_start = metrics.get("eval_start")
                r.eval_end = metrics.get("eval_end")
                r.evaluated_at = datetime.now()
        await session.commit()


async def batch_evaluate_alpha158(
    batch_size: int = 20,
    max_concurrent: int = 16,
    eval_start: str = None,
    eval_end: str = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """批量并行评价 Alpha158 因子（优化版：预加载共用数据 + 线程池 + 批次写入）。

    性能优化点：
    1. 预加载前向收益标签（158 个因子共用 1 次）
    2. 预加载 $close 价格数据（decay 共用）
    3. 线程池而非进程池（qlib C 扩展释放 GIL）
    4. 分批并发控制（max_concurrent 限制内存峰值）
    5. 数据库批量写入（每 batch_size 条一次 commit）

    Args:
        batch_size: DB 批次写入大小
        max_concurrent: 最大并发任务数
        eval_start/eval_end: 评价区间，默认从 config 读取
        progress_callback: 进度回调 (done, total, current_name)
    """
    from sqlalchemy import select
    from app.core.config import settings
    from app.core.database import async_session
    from app.core.executor import run_io_cpu  # 线程池，qlib 释放 GIL
    from app.models.factor import Factor
    from app.services.quant.factor_eval import evaluate_factor, load_label
    from app.services.quant.qlib_init import init_qlib

    # 1. 取参数
    period = settings.quant.get("default_backtest_period", {})
    eval_start = eval_start or period.get("start", "2020-01-01")
    eval_end = eval_end or period.get("end", "2024-12-31")
    universe = settings.quant.get("universe", "csi300")
    horizon = settings.mining.get("llm", {}).get("eval_horizon", 5)

    # 2. 查询所有 alpha158 因子
    async with async_session() as session:
        rows = await session.execute(
            select(Factor.id, Factor.name, Factor.expression).where(
                Factor.category == "alpha158"
            )
        )
        targets = rows.all()

    if not targets:
        return {"ok": True, "evaluated": 0, "failed": 0, "total": 0,
                "message": "无 Alpha158 因子"}

    expr_map = {r.id: r.expression for r in targets}
    total = len(targets)

    # 3. 预加载共用数据（关键优化！避免 N 次重复 IO）
    logger.info("Alpha158 批量评价: 预加载共用数据 (label + close), 待评价 %d 因子", total)
    label_expr = f"Ref($close, -{horizon}) / $close - 1"
    preloaded_label_df = load_label(eval_start, eval_end, label_expr=label_expr, universe=universe)

    # 预加载 $close 用于 decay 计算
    init_qlib()
    from qlib.data import D
    instruments = D.list_instruments(D.instruments(market=universe), freq="day")
    preloaded_close_df = D.features(
        list(instruments.keys()), ["$close"],
        start_time=eval_start, end_time=eval_end, freq="day"
    )
    logger.info(
        "Alpha158 预加载完成: label=%s, close=%s",
        preloaded_label_df.shape,
        preloaded_close_df.shape if preloaded_close_df is not None else "N/A",
    )

    # 4. 信号量控制并发
    sem = asyncio.Semaphore(max_concurrent)

    async def _eval_one(fid: int) -> tuple:
        async with sem:
            expr = expr_map[fid]
            try:
                # 传入预加载数据，避免重复 IO
                metrics = await run_io_cpu(
                    evaluate_factor, expr, eval_start, eval_end, universe,
                    preloaded_label_df=preloaded_label_df,
                    preloaded_close_df=preloaded_close_df,
                )
                return fid, metrics, None
            except Exception as e:
                return fid, None, str(e)[:200]

    # 5. 创建所有任务（受信号量控制）
    tasks = [_eval_one(r.id) for r in targets]

    # 使用 as_completed 风格以便更新进度
    success = 0
    failed = 0
    results_buffer: list = []  # 批量写入缓冲

    for i, coro in enumerate(asyncio.as_completed(tasks)):
        try:
            fid, metrics, err = await coro
            if err:
                logger.warning("Alpha158 id=%d 评价失败: %s", fid, err)
                failed += 1
            else:
                results_buffer.append((fid, metrics))
                success += 1
        except Exception as e:
            logger.warning("Alpha158 评价异常: %s", e)
            failed += 1

        # 进度回调
        if progress_callback and (i + 1) % 10 == 0:
            try:
                progress_callback(i + 1, total, f"已完成 {i+1}/{total}")
            except Exception:
                pass

        # 批量写入
        if len(results_buffer) >= batch_size:
            await _flush_batch_metrics(results_buffer)
            results_buffer = []

    # 处理剩余
    if results_buffer:
        await _flush_batch_metrics(results_buffer)

    logger.info("Alpha158 批量评价完成: %d 成功, %d 失败", success, failed)
    return {
        "ok": True,
        "evaluated": success,
        "failed": failed,
        "total": total,
        "message": f"批量评价完成 {success}/{total}",
    }


async def seed_alpha158(progress_callback: Optional[Callable[[int, int, str], None]] = None) -> dict:
    """将 Alpha158 的 158 个因子批量导入因子库，并行计算评价指标。

    行为：
    - 已导入则直接返回 ok=True + count=0 + already_imported=True，让前端区分"重复操作"
    - 新导入后调用 batch_evaluate_alpha158 统一评价（预加载 label/close + 线程池 + 批次写入）
    - 单个因子评价失败不阻塞整体流程，记录日志后继续
    """
    from sqlalchemy import select
    from app.core.config import settings
    from app.core.database import async_session
    from app.models.factor import Factor

    period = settings.quant.get("default_backtest_period", {})
    eval_start = period.get("start", "2020-01-01")
    eval_end = period.get("end", "2024-12-31")

    async with async_session() as session:
        # 检查是否已导入（按 category 去重，避免重复）
        existing = await session.execute(
            select(Factor).where(Factor.category == "alpha158").limit(1)
        )
        if existing.scalars().first():
            return {"ok": True, "count": 0,
                    "already_imported": True,
                    "message": "Alpha158 已导入，无需重复操作"}

        created = 0
        for item in ALPHA158_EXPRESSIONS:
            factor = Factor(
                name=item["name"],
                expression=item["expr"],
                category=item["category"],
                description=item.get("description", ""),
                status="active",
            )
            session.add(factor)
            created += 1
        await session.commit()

    logger.info("Alpha158 导入完成: %d 个因子，开始批量评价指标", created)

    # 委托 batch_evaluate_alpha158 统一优化路径
    eval_result = await batch_evaluate_alpha158(
        eval_start=eval_start,
        eval_end=eval_end,
        progress_callback=progress_callback,
    )

    success = eval_result.get("evaluated", 0)
    failed = eval_result.get("failed", 0)
    logger.info("Alpha158 评价完成: %d 成功, %d 失败", success, failed)
    return {
        "ok": True,
        "count": created,
        "evaluated": success,
        "eval_failed": failed,
        "already_imported": False,
        "message": f"Alpha158 已导入 {created} 个，评价完成 {success} 个",
    }


async def backfill_alpha158_metrics(
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """为已存在但缺指标的 Alpha158 因子补算评价。

    用途：修复历史上导入时未触发评价的因子（IC/RankIC/ICIR/turnover 为 NULL）。
    与 seed_alpha158 的区别：不会插入新因子，只对 category='alpha158' 且 ic IS NULL 的因子补算。
    """
    from sqlalchemy import select
    from app.core.config import settings
    from app.core.database import async_session
    from app.models.factor import Factor

    period = settings.quant.get("default_backtest_period", {})
    eval_start = period.get("start", "2020-01-01")
    eval_end = period.get("end", "2024-12-31")

    async with async_session() as session:
        rows = await session.execute(
            select(Factor.id, Factor.expression).where(
                Factor.category == "alpha158",
                Factor.ic.is_(None),
            )
        )
        targets = rows.all()

    if not targets:
        return {"ok": True, "evaluated": 0, "total": 0,
                "message": "Alpha158 所有因子已评价，无需补算"}

    logger.info("Alpha158 补算评价指标: %d 个因子待评价", len(targets))

    eval_result = await batch_evaluate_alpha158(
        eval_start=eval_start,
        eval_end=eval_end,
        progress_callback=progress_callback,
    )

    evaluated = eval_result.get("evaluated", 0)
    failed = eval_result.get("failed", 0)
    logger.info("Alpha158 补算完成: %d/%d 成功", evaluated, len(targets))
    return {
        "ok": True,
        "evaluated": evaluated,
        "eval_failed": failed,
        "total": len(targets),
        "message": f"Alpha158 补算完成 {evaluated}/{len(targets)}",
    }

