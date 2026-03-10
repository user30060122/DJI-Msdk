package dji.sampleV5.aircraft

import android.content.Intent
import android.os.Bundle
import androidx.activity.viewModels
import androidx.lifecycle.lifecycleScope
import dji.sampleV5.aircraft.databinding.ActivityAircraftMainBinding
import dji.sampleV5.aircraft.models.MSDKInfoVm
import dji.sampleV5.aircraft.models.MSDKManagerVM
import dji.sampleV5.aircraft.models.globalViewModels
import dji.sampleV5.aircraft.util.PgyerUpdateManager
import dji.v5.common.utils.GeoidManager
import dji.v5.manager.SDKManager
import dji.v5.ux.core.communication.DefaultGlobalPreferences
import dji.v5.ux.core.communication.GlobalPreferencesManager
import dji.v5.ux.core.util.UxSharedPreferencesUtil
import kotlinx.coroutines.launch

/**
 * 航点飞行系统主界面
 */
class DJIAircraftMainActivity : DJIMainActivity() {

    private lateinit var aircraftBinding: ActivityAircraftMainBinding
    private val msdkInfoVm: MSDKInfoVm by viewModels()
    private val msdkManagerVM: MSDKManagerVM by globalViewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // 使用自定义布局
        aircraftBinding = ActivityAircraftMainBinding.inflate(layoutInflater)
        setContentView(aircraftBinding.root)

        // 初始化SDK信息显示
        initCustomViews()

        // 检查版本更新
        checkForUpdate()
    }

    private fun initCustomViews() {
        // SDK版本
        aircraftBinding.textCoreInfo.text = SDKManager.getInstance().sdkVersion ?: "未知"

        // 包名
        aircraftBinding.textPackageName.text = packageName

        // 监听产品信息
        msdkInfoVm.msdkInfo.observe(this) { info ->
            info?.let {
                aircraftBinding.textProductType.text = it.productType?.name ?: "未连接"
            }
        }

        // 监听注册状态
        msdkManagerVM.lvRegisterState.observe(this) { state ->
            val isRegistered = state?.first ?: false
            aircraftBinding.textRegisterState.text = if (isRegistered) "已注册" else "未注册"

            // 只有注册成功才能使用航点飞行
            aircraftBinding.btnWaypointFlight.isEnabled = isRegistered
        }

        // 设置航点飞行按钮
        aircraftBinding.btnWaypointFlight.setOnClickListener {
            startActivity(Intent(this, WaypointFlightActivity::class.java))
        }
    }

    private fun checkForUpdate() {
        lifecycleScope.launch {
            PgyerUpdateManager.checkUpdate(this@DJIAircraftMainActivity)
        }
    }

    override fun prepareUxActivity() {
        UxSharedPreferencesUtil.initialize(this)
        GlobalPreferencesManager.initialize(DefaultGlobalPreferences(this))
        GeoidManager.getInstance().init(this)

        // 不启用默认布局和小部件列表
        // enableDefaultLayout(DefaultLayoutActivity::class.java)
        // enableWidgetList(WidgetsActivity::class.java)
    }

    override fun prepareTestingToolsActivity() {
        // 只启用航点飞行功能
        // enableTestingTools(AircraftTestingToolsActivity::class.java)
        // enableWaypointFlight(WaypointFlightActivity::class.java)
    }
}
