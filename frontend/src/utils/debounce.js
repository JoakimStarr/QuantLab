// 通用防抖：返回包装函数，间隔内连续调用只执行最后一次。
// 用于 el-select remote-method 等本身不内置 debounce 的输入搜索场景，
// 避免每个键入字符都触发一次网络请求。
export function debounce(fn, wait = 300) {
  let timer = null
  return function (...args) {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => {
      timer = null
      fn.apply(this, args)
    }, wait)
  }
}