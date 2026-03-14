package dji.sampleV5.aircraft

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.location.Location
import android.os.Bundle
import android.widget.SeekBar
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.gms.location.*
import dji.sampleV5.aircraft.databinding.ActivityWaypointFlightBinding
import dji.sampleV5.aircraft.models.BasicAircraftControlVM
import dji.sampleV5.aircraft.models.WaypointFlightVM
import dji.sampleV5.aircraft.util.MqttManager
import dji.sampleV5.aircraft.util.ToastUtils
import dji.sdk.keyvalue.value.common.ComponentIndexType
import dji.v5.manager.datacenter.MediaDataCenter
import dji.v5.manager.interfaces.ICameraStreamManager
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class WaypointFlightActivity : AppCompatActivity() {

    private lateinit var binding: ActivityWaypointFlightBinding
    private val waypointVM: WaypointFlightVM by viewModels()
    private val aircraftControlVM: BasicAircraftControlVM by viewModels()

    private lateinit var fusedLocationClient: FusedLocationProviderClient
    private lateinit var locationCallback: LocationCallback
    private var currentLocation: Location? = null

    // MQTT状态上报
    private var statusReportJob: Job? = null

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
            binding.fpvWidget.updateVideoSource(availableCameras[currentCameraIndex])
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

    private val locationPermissionRequest = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { permissions ->
        when {
            permissions.getOrDefault(Manifest.permission.ACCESS_FINE_LOCATION, false) -> {
                startLocationUpdates()
            }
            permissions.getOrDefault(Manifest.permission.ACCESS_COARSE_LOCATION, false) -> {
                startLocationUpdates()
            }
            else -> {
                ToastUtils.showToast("需要位置权限才能使用此功能")
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityWaypointFlightBinding.inflate(layoutInflater)
        setContentView(binding.root)

        initLocationServices()
        initViews()
        observeViewModel()
        checkLocationPermission()

        // 注册相机流监听器以初始化FPV视频源
        MediaDataCenter.getInstance().cameraStreamManager.addAvailableCameraUpdatedListener(availableCameraUpdatedListener)

        // 开始实时更新飞机GPS位置
        waypointVM.startAircraftLocationUpdates()

        // 初始化MQTT
        initMqtt()
    }

    private fun initMqtt() {
        MqttManager.init(this)

        // 接收电脑端指令
        MqttManager.onCommandReceived = { action, data ->
            runOnUiThread {
                when (action) {
                    "start_mission" -> {
                        val startLat = data.optDouble("start_lat")
                        val startLng = data.optDouble("start_lng")
                        val endLat = data.optDouble("end_lat")
                        val endLng = data.optDouble("end_lng")
                        val altitude = data.optDouble("altitude", 10.0)
                        val speed = data.optDouble("speed", 2.0)
                        val stayDuration = data.optInt("stay_duration", 5)
                        val mode = data.optString("mode", "auto")

                        waypointVM.setStartPointFromCoords(startLat, startLng)
                        waypointVM.setEndPointFromCoords(endLat, endLng)
                        waypointVM.flightAltitude.value = altitude
                        waypointVM.flightSpeed.value = speed
                        waypointVM.stayDuration.value = stayDuration

                        waypointVM.startMission(
                            autoMode = (mode == "auto"),
                            takeOffCallback = { cb -> aircraftControlVM.startTakeOff(cb) },
                            landingCallback = { cb -> aircraftControlVM.startLanding(cb) }
                        )
                        ToastUtils.showToast("收到远程指令，开始执行任务")
                    }
                    "stop_mission" -> {
                        waypointVM.stopMission()
                        ToastUtils.showToast("收到远程停止指令")
                    }
                }
            }
        }

        // 连接状态变化
        MqttManager.onConnectionChanged = { connected ->
            runOnUiThread {
                ToastUtils.showToast(if (connected) "MQTT已连接 ID:${MqttManager.droneId}" else "MQTT断开连接")
            }
        }

        // 每2秒上报一次飞机状态
        statusReportJob = lifecycleScope.launch {
            while (true) {
                delay(2000)
                val loc = waypointVM.currentLocation.value
                val status = waypointVM.missionStatus.value ?: "待命"
                val isFlying = waypointVM.isFlying.value ?: false
                MqttManager.publishStatus(
                    lat = loc?.latitude ?: 0.0,
                    lng = loc?.longitude ?: 0.0,
                    altitude = loc?.altitude ?: 0.0,
                    missionStatus = status,
                    isFlying = isFlying
                )
            }
        }
    }

    private fun initLocationServices() {
        fusedLocationClient = LocationServices.getFusedLocationProviderClient(this)

        locationCallback = object : LocationCallback() {
            override fun onLocationResult(locationResult: LocationResult) {
                locationResult.lastLocation?.let { location ->
                    currentLocation = location
                    // 不再更新ViewModel的位置，因为已经使用飞机GPS实时更新
                }
            }
        }
    }

    private fun initViews() {
        // Set Start Point Button - 使用飞机GPS
        binding.btnSetStartPoint.setOnClickListener {
            waypointVM.setStartPointFromAircraft()
            ToastUtils.showToast("正在获取飞机位置作为起点...")
        }

        // Copy Start Point Button
        binding.btnCopyStartPoint.setOnClickListener {
            val point = waypointVM.startPoint.value
            if (point != null) {
                val text = "${point.latitude},${point.longitude}"
                copyToClipboard(text, "起点坐标")
            } else {
                ToastUtils.showToast("起点未设置")
            }
        }

        // Set End Point Button - 使用飞机GPS
        binding.btnSetEndPoint.setOnClickListener {
            waypointVM.setEndPointFromAircraft()
            ToastUtils.showToast("正在获取飞机位置作为终点...")
        }

        // Copy End Point Button
        binding.btnCopyEndPoint.setOnClickListener {
            val point = waypointVM.endPoint.value
            if (point != null) {
                val text = "${point.latitude},${point.longitude}"
                copyToClipboard(text, "终点坐标")
            } else {
                ToastUtils.showToast("终点未设置")
            }
        }

        // Altitude SeekBar
        binding.seekbarAltitude.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: SeekBar?, progress: Int, fromUser: Boolean) {
                val altitude = progress + 5.0 // 5-50 meters
                waypointVM.flightAltitude.value = altitude
                binding.tvAltitudeValue.text = "$altitude m"
            }

            override fun onStartTrackingTouch(seekBar: SeekBar?) {}
            override fun onStopTrackingTouch(seekBar: SeekBar?) {}
        })

        // Duration SeekBar
        binding.seekbarDuration.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: SeekBar?, progress: Int, fromUser: Boolean) {
                waypointVM.stayDuration.value = progress
                binding.tvDurationValue.text = "$progress s"
            }

            override fun onStartTrackingTouch(seekBar: SeekBar?) {}
            override fun onStopTrackingTouch(seekBar: SeekBar?) {}
        })

        // Flight Speed EditText
        binding.etFlightSpeed.setOnFocusChangeListener { _, hasFocus ->
            if (!hasFocus) {
                val speedText = binding.etFlightSpeed.text.toString()
                val speed = speedText.toDoubleOrNull()
                if (speed != null && speed in 0.1..15.0) {
                    waypointVM.flightSpeed.value = speed
                } else {
                    ToastUtils.showToast("速度必须在 0.1-15 m/s 之间")
                    binding.etFlightSpeed.setText("2.0")
                    waypointVM.flightSpeed.value = 2.0
                }
            }
        }

        // Start Mission Button
        binding.btnStartMission.setOnClickListener {
            // Validate speed before starting mission
            val speedText = binding.etFlightSpeed.text.toString()
            val speed = speedText.toDoubleOrNull()
            if (speed == null || speed !in 0.1..15.0) {
                ToastUtils.showToast("请输入有效的飞行速度 (0.1-15 m/s)")
                return@setOnClickListener
            }
            waypointVM.flightSpeed.value = speed

            if (waypointVM.startPoint.value == null || waypointVM.endPoint.value == null) {
                ToastUtils.showToast("请先设置起点和终点")
                return@setOnClickListener
            }

            // 显示模式选择对话框
            showModeSelectionDialog()
        }

        // Next Step Button
        binding.btnNextStep.setOnClickListener {
            waypointVM.nextStep()
        }

        // Stop Mission Button
        binding.btnStopMission.setOnClickListener {
            waypointVM.stopMission()
            ToastUtils.showToast("任务已停止")
        }

        // 设置图传画面点击放大
        binding.fpvWidget.setOnClickListener {
            startActivity(Intent(this, FPVFullscreenActivity::class.java))
        }

        // 摄像头切换按钮
        binding.btnSwitchCamera.setOnClickListener {
            if (availableCameras.size > 1) {
                currentCameraIndex = (currentCameraIndex + 1) % availableCameras.size
                updateCameraSource()
                updateSwitchButton()
            }
        }
    }

    private fun observeViewModel() {
        waypointVM.currentLocation.observe(this) { location ->
            binding.tvCurrentLocation.text = location?.toString() ?: "未获取"
        }

        waypointVM.startPoint.observe(this) { point ->
            binding.tvStartPoint.text = point?.toString() ?: "未设置"
        }

        waypointVM.endPoint.observe(this) { point ->
            binding.tvEndPoint.text = point?.toString() ?: "未设置"
        }

        waypointVM.missionStatus.observe(this) { status ->
            binding.tvMissionStatus.text = status
        }

        waypointVM.isFlying.observe(this) { isFlying ->
            binding.btnStartMission.isEnabled = !isFlying
            binding.btnStopMission.isEnabled = isFlying
        }

        waypointVM.canProceed.observe(this) { canProceed ->
            binding.btnNextStep.isEnabled = canProceed
        }

        // 监听高度变化（来自MQTT远程命令）
        waypointVM.flightAltitude.observe(this) { altitude ->
            val progress = (altitude - 5.0).toInt().coerceIn(0, 45)
            binding.seekbarAltitude.progress = progress
            binding.tvAltitudeValue.text = "$altitude m"
        }

        // 监听速度变化（来自MQTT远程命令）
        waypointVM.flightSpeed.observe(this) { speed ->
            binding.etFlightSpeed.setText(speed.toString())
        }

        // 监听停留时间变化（来自MQTT远程命令）
        waypointVM.stayDuration.observe(this) { duration ->
            binding.seekbarDuration.progress = duration
            binding.tvDurationValue.text = "$duration s"
        }
    }

    private fun checkLocationPermission() {
        when {
            ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.ACCESS_FINE_LOCATION
            ) == PackageManager.PERMISSION_GRANTED -> {
                startLocationUpdates()
            }
            else -> {
                locationPermissionRequest.launch(
                    arrayOf(
                        Manifest.permission.ACCESS_FINE_LOCATION,
                        Manifest.permission.ACCESS_COARSE_LOCATION
                    )
                )
            }
        }
    }

    private fun startLocationUpdates() {
        val locationRequest = LocationRequest.create().apply {
            interval = 2000
            fastestInterval = 1000
            priority = LocationRequest.PRIORITY_HIGH_ACCURACY
        }

        try {
            fusedLocationClient.requestLocationUpdates(
                locationRequest,
                locationCallback,
                mainLooper
            )
        } catch (e: SecurityException) {
            ToastUtils.showToast("位置权限被拒绝")
        }
    }

    private fun showModeSelectionDialog() {
        val builder = android.app.AlertDialog.Builder(this)
        builder.setTitle("选择执行模式")
        builder.setMessage("请选择任务执行方式：")
        builder.setPositiveButton("全自主执行") { _, _ ->
            waypointVM.startMission(
                autoMode = true,
                takeOffCallback = { callback ->
                    aircraftControlVM.startTakeOff(callback)
                },
                landingCallback = { callback ->
                    aircraftControlVM.startLanding(callback)
                }
            )
        }
        builder.setNegativeButton("分步执行") { _, _ ->
            waypointVM.startMission(
                autoMode = false,
                takeOffCallback = { callback ->
                    aircraftControlVM.startTakeOff(callback)
                },
                landingCallback = { callback ->
                    aircraftControlVM.startLanding(callback)
                }
            )
        }
        builder.setCancelable(true)
        builder.show()
    }

    override fun onResume() {
        super.onResume()
        if (ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.ACCESS_FINE_LOCATION
            ) == PackageManager.PERMISSION_GRANTED
        ) {
            startLocationUpdates()
        }
    }

    override fun onPause() {
        super.onPause()
        fusedLocationClient.removeLocationUpdates(locationCallback)
    }

    override fun onDestroy() {
        super.onDestroy()
        waypointVM.stopMission()
        waypointVM.stopAircraftLocationUpdates()
        statusReportJob?.cancel()
        MqttManager.disconnect()
        MediaDataCenter.getInstance().cameraStreamManager.removeAvailableCameraUpdatedListener(availableCameraUpdatedListener)
    }

    private fun copyToClipboard(text: String, label: String) {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val clip = ClipData.newPlainText(label, text)
        clipboard.setPrimaryClip(clip)
        ToastUtils.showToast("已复制$label: $text")
    }
}
