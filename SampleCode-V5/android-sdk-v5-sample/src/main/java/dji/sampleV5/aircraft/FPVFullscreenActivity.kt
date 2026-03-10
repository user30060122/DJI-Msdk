package dji.sampleV5.aircraft

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import dji.sampleV5.aircraft.databinding.ActivityFpvFullscreenBinding
import dji.sdk.keyvalue.value.common.ComponentIndexType
import dji.v5.manager.datacenter.MediaDataCenter
import dji.v5.manager.interfaces.ICameraStreamManager

/**
 * 全屏图传查看Activity
 */
class FPVFullscreenActivity : AppCompatActivity() {

    private lateinit var binding: ActivityFpvFullscreenBinding

    // 摄像头管理
    private var availableCameras = mutableListOf<ComponentIndexType>()
    private var currentCameraIndex = 0

    private val availableCameraUpdatedListener = object : ICameraStreamManager.AvailableCameraUpdatedListener {
        override fun onAvailableCameraUpdated(availableCameraList: MutableList<ComponentIndexType>) {
            runOnUiThread {
                availableCameras.clear()
                availableCameras.addAll(availableCameraList)

                if (availableCameras.isNotEmpty()) {
                    // 优先使用主相机或左相机
                    currentCameraIndex = when {
                        availableCameras.contains(ComponentIndexType.LEFT_OR_MAIN) ->
                            availableCameras.indexOf(ComponentIndexType.LEFT_OR_MAIN)
                        availableCameras.contains(ComponentIndexType.FPV) ->
                            availableCameras.indexOf(ComponentIndexType.FPV)
                        else -> 0
                    }
                    updateCameraSource()
                    updateSwitchButton()
                }
            }
        }

        override fun onCameraStreamEnableUpdate(cameraStreamEnableMap: MutableMap<ComponentIndexType, Boolean>) {
            // Not needed for basic FPV display
        }
    }

    private fun updateCameraSource() {
        if (availableCameras.isNotEmpty() && currentCameraIndex < availableCameras.size) {
            binding.fpvWidgetFullscreen.updateVideoSource(availableCameras[currentCameraIndex])
        }
    }

    private fun updateSwitchButton() {
        binding.btnSwitchCamera.isEnabled = availableCameras.size > 1
        if (availableCameras.isNotEmpty() && currentCameraIndex < availableCameras.size) {
            val cameraName = when (availableCameras[currentCameraIndex]) {
                ComponentIndexType.LEFT_OR_MAIN -> "主相机"
                ComponentIndexType.FPV -> "FPV"
                ComponentIndexType.RIGHT -> "右相机"
                ComponentIndexType.UP -> "上相机"
                else -> "相机${currentCameraIndex + 1}"
            }
            binding.btnSwitchCamera.text = cameraName
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityFpvFullscreenBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // 隐藏状态栏和导航栏
        window.decorView.systemUiVisibility = (
            android.view.View.SYSTEM_UI_FLAG_FULLSCREEN
            or android.view.View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
            or android.view.View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        )

        // 关闭按钮
        binding.btnClose.setOnClickListener {
            finish()
        }

        // 摄像头切换按钮
        binding.btnSwitchCamera.setOnClickListener {
            if (availableCameras.size > 1) {
                currentCameraIndex = (currentCameraIndex + 1) % availableCameras.size
                updateCameraSource()
                updateSwitchButton()
            }
        }

        // 注册相机流监听器以初始化FPV视频源
        MediaDataCenter.getInstance().cameraStreamManager.addAvailableCameraUpdatedListener(availableCameraUpdatedListener)
    }

    override fun onDestroy() {
        super.onDestroy()
        MediaDataCenter.getInstance().cameraStreamManager.removeAvailableCameraUpdatedListener(availableCameraUpdatedListener)
    }
}
