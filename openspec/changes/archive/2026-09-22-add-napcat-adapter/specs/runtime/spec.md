## ADDED Requirements

### Requirement: 前端装配选择
应用 SHALL 依据配置的平台选择项装配对应平台前端，QQ 官方前端与 NapCat 前端 SHALL 可通过配置互换；选择项缺省时 SHALL 装配 QQ 官方前端（兼容既有配置）；选择项为未知值时 SHALL 启动即报错退出并提示可选值；所选前端的连接参数 SHALL 按各自要求校验。

#### Scenario: 选择 NapCat 前端
- **WHEN** 配置的选择项为 napcat 且连接参数齐备
- **THEN** 应用装配 NapCat 前端启动，业务层无感

#### Scenario: 缺省装配官方前端
- **WHEN** 配置中不存在选择项但存在官方凭据
- **THEN** 应用装配 QQ 官方前端，行为与既有版本一致

#### Scenario: 未知选择项快速失败
- **WHEN** 配置的选择项不是任何受支持的值
- **THEN** 启动报错退出，提示受支持的可选值
