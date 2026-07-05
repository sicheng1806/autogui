# 自动化项目

自动化脚本集。

## 环境部署

0. 克隆项目到本地

```sh
git clone https://github.com/sicheng1806/autogui.git
```

如果没有`git`工具需要先下载[git](https://git-scm.com/)工具。

1. 首先先下载项目管理工具[uv](https://uv.doczh.com/getting-started/installation/#__tabbed_1_2)

**windows**

- 使用 irm 下载脚本并通过 iex 执行：
  ```sh
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```
  更改执行策略允许运行来自互联网的脚本。

- 通过在 URL 中包含版本号来请求特定版本：

  ```sh
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/0.7.4/install.ps1 | iex"
  ```

2. 安装项目

```sh
uv sync
```

3. 运行相关脚本，例如：

```sh
uv run damai 张三 李四
```


## 自动化脚本

- [x] 大麦抢票脚本: `uv run damai 抢票用户的名字1 名字2`
