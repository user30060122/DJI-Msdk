package dji.sampleV5.aircraft.models

import android.location.Location
import androidx.lifecycle.MutableLiveData
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dji.sdk.keyvalue.key.FlightControllerKey
import dji.sdk.keyvalue.key.FlightAssistantKey
import dji.sdk.keyvalue.key.KeyTools
import dji.sdk.keyvalue.value.common.EmptyMsg
import dji.sdk.keyvalue.value.common.LocationCoordinate2D
import dji.v5.common.callback.CommonCallbacks
import dji.v5.common.error.IDJIError
import dji.v5.manager.KeyManager
import dji.v5.manager.aircraft.virtualstick.VirtualStickManager
import dji.v5.manager.aircraft.virtualstick.Stick
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import kotlin.math.*

/**
 * ViewModel for Waypoint Flight using Virtual Stick
 */
class WaypointFlightVM : ViewModel() {

    // GPS coordinates
    val startPoint = MutableLiveData<GPSPoint?>()
    val endPoint = MutableLiveData<GPSPoint?>()
    val currentLocation = MutableLiveData<GPSPoint?>()

    // Flight parameters
    val flightAltitude = MutableLiveData(10.0) // meters
    val stayDuration = MutableLiveData(5) // seconds
    val flightSpeed = MutableLiveData(2.0) // m/s

    // Mission status
    val missionStatus = MutableLiveData("待命")
    val isFlying = MutableLiveData(false)
    val currentStep = MutableLiveData(0)
    val canProceed = MutableLiveData(false)
    var isAutoMode = false // true=全自主执行, false=分步执行

    private var missionJob: Job? = null
    private var locationUpdateJob: Job? = null
    private lateinit var takeOffCallback: (CommonCallbacks.CompletionCallbackWithParam<EmptyMsg>) -> Unit
    private lateinit var landingCallback: (CommonCallbacks.CompletionCallbackWithParam<EmptyMsg>) -> Unit

    data class GPSPoint(
        val latitude: Double,
        val longitude: Double,
        val altitude: Double = 0.0
    ) {
        override fun toString(): String {
            return "纬度: %.6f, 经度: %.6f".format(latitude, longitude)
        }
    }

    fun setStartPoint(location: Location) {
        startPoint.value = GPSPoint(location.latitude, location.longitude, location.altitude)
    }

    fun setStartPointFromCoords(lat: Double, lng: Double) {
        startPoint.value = GPSPoint(lat, lng, 0.0)
    }

    fun setEndPointFromCoords(lat: Double, lng: Double) {
        endPoint.value = GPSPoint(lat, lng, 0.0)
    }

    fun setStartPointFromAircraft() {
        viewModelScope.launch {
            var retryCount = 0
            while (retryCount < 3) {
                val location = suspendCancellableCoroutine<LocationCoordinate2D?> { continuation ->
                    KeyManager.getInstance().getValue(
                        KeyTools.createKey(FlightControllerKey.KeyAircraftLocation),
                        object : CommonCallbacks.CompletionCallbackWithParam<LocationCoordinate2D> {
                            override fun onSuccess(location: LocationCoordinate2D?) {
                                continuation.resume(location)
                            }
                            override fun onFailure(error: IDJIError) {
                                continuation.resume(null)
                            }
                        }
                    )
                }

                if (location != null) {
                    startPoint.postValue(GPSPoint(location.latitude, location.longitude, 0.0))
                    missionStatus.postValue("起点已设置")
                    return@launch
                }

                retryCount++
                if (retryCount < 3) {
                    missionStatus.postValue("获取位置失败，重试中... ($retryCount/3)")
                    delay(1000)
                } else {
                    missionStatus.postValue("获取飞机位置失败，请重试")
                }
            }
        }
    }

    fun setEndPoint(location: Location) {
        endPoint.value = GPSPoint(location.latitude, location.longitude, location.altitude)
    }

    fun setEndPointFromAircraft() {
        viewModelScope.launch {
            var retryCount = 0
            while (retryCount < 3) {
                val location = suspendCancellableCoroutine<LocationCoordinate2D?> { continuation ->
                    KeyManager.getInstance().getValue(
                        KeyTools.createKey(FlightControllerKey.KeyAircraftLocation),
                        object : CommonCallbacks.CompletionCallbackWithParam<LocationCoordinate2D> {
                            override fun onSuccess(location: LocationCoordinate2D?) {
                                continuation.resume(location)
                            }
                            override fun onFailure(error: IDJIError) {
                                continuation.resume(null)
                            }
                        }
                    )
                }

                if (location != null) {
                    endPoint.postValue(GPSPoint(location.latitude, location.longitude, 0.0))
                    missionStatus.postValue("终点已设置")
                    return@launch
                }

                retryCount++
                if (retryCount < 3) {
                    missionStatus.postValue("获取位置失败，重试中... ($retryCount/3)")
                    delay(1000)
                } else {
                    missionStatus.postValue("获取飞机位置失败，请重试")
                }
            }
        }
    }

    fun updateCurrentLocation(location: Location) {
        currentLocation.value = GPSPoint(location.latitude, location.longitude, location.altitude)
    }

    /**
     * 开始实时更新飞机GPS位置
     */
    fun startAircraftLocationUpdates() {
        locationUpdateJob?.cancel()
        locationUpdateJob = viewModelScope.launch {
            while (true) {
                val location = suspendCancellableCoroutine<LocationCoordinate2D?> { continuation ->
                    KeyManager.getInstance().getValue(
                        KeyTools.createKey(FlightControllerKey.KeyAircraftLocation),
                        object : CommonCallbacks.CompletionCallbackWithParam<LocationCoordinate2D> {
                            override fun onSuccess(location: LocationCoordinate2D?) {
                                continuation.resume(location)
                            }
                            override fun onFailure(error: IDJIError) {
                                continuation.resume(null)
                            }
                        }
                    )
                }

                if (location != null) {
                    currentLocation.postValue(GPSPoint(location.latitude, location.longitude, 0.0))
                }

                delay(100) // 每100ms更新一次
            }
        }
    }

    /**
     * 停止实时更新飞机GPS位置
     */
    fun stopAircraftLocationUpdates() {
        locationUpdateJob?.cancel()
        locationUpdateJob = null
    }

    fun startMission(
        autoMode: Boolean,
        takeOffCallback: (CommonCallbacks.CompletionCallbackWithParam<EmptyMsg>) -> Unit,
        landingCallback: (CommonCallbacks.CompletionCallbackWithParam<EmptyMsg>) -> Unit
    ) {
        val start = startPoint.value
        val end = endPoint.value

        if (start == null || end == null) {
            missionStatus.value = "错误：起点或终点未设置"
            return
        }

        this.isAutoMode = autoMode
        this.takeOffCallback = takeOffCallback
        this.landingCallback = landingCallback

        currentStep.value = 0
        isFlying.value = true

        if (autoMode) {
            // 全自主执行模式
            canProceed.value = false
            missionStatus.value = "全自主模式：任务开始执行..."
            executeAutoMission()
        } else {
            // 分步执行模式
            canProceed.value = true
            missionStatus.value = "分步模式：任务已准备，点击下一步开始"
        }
    }

    private fun executeAutoMission() {
        missionJob = viewModelScope.launch {
            try {
                val start = startPoint.value!!
                val end = endPoint.value!!

                // 步骤1: 起飞（起飞时不启用虚拟摇杆）
                missionStatus.value = "步骤1/12: 起飞中..."
                takeoff(takeOffCallback)
                delay(5000)

                // 步骤2: 启用虚拟摇杆
                missionStatus.value = "步骤2/12: 启用虚拟摇杆..."
                enableVirtualStick()
                delay(1000)

                // 步骤3: 爬升
                missionStatus.value = "步骤3/12: 爬升到 ${flightAltitude.value}m..."
                climbToAltitude(flightAltitude.value ?: 10.0)

                // 步骤4: 转向终点
                missionStatus.value = "步骤4/12: 机头转向终点..."
                val bearing1 = calculateBearing(start, end)
                rotateToHeading(bearing1)

                // 步骤5: 飞往终点
                missionStatus.value = "步骤5/12: 飞往终点..."
                flyToPoint(start, end)

                // 步骤6: 降落
                missionStatus.value = "步骤6/12: 在终点降落..."
                land(landingCallback)

                // 步骤8: 停留
                val duration = stayDuration.value ?: 5
                missionStatus.value = "步骤8/12: 停留 ${duration} 秒..."
                delay(duration * 1000L)

                // 步骤9: 起飞返回
                missionStatus.value = "步骤9/12: 起飞返回..."
                takeoff(takeOffCallback)
                delay(5000)

                // 步骤10: 启用虚拟摇杆
                missionStatus.value = "步骤10/12: 启用虚拟摇杆..."
                enableVirtualStick()
                delay(1000)

                // 步骤11: 爬升
                missionStatus.value = "步骤11/12: 爬升到 ${flightAltitude.value}m..."
                climbToAltitude(flightAltitude.value ?: 10.0)

                // 步骤12: 转向起点
                missionStatus.value = "步骤12/12: 机头转向起点..."
                val bearing2 = calculateBearing(end, start)
                rotateToHeading(bearing2)

                // 步骤13: 返回起点
                missionStatus.value = "步骤13/12: 返回起点..."
                flyToPoint(end, start)

                // 步骤14: 降落
                missionStatus.value = "步骤14/12: 在起点降落..."
                land(landingCallback)

                // 完成
                disableVirtualStick()
                missionStatus.value = "任务完成！"
                isFlying.value = false
            } catch (e: Exception) {
                missionStatus.value = "任务失败: ${e.message}"
                isFlying.value = false
            }
        }
    }

    fun nextStep() {
        val step = currentStep.value ?: 0
        canProceed.value = false

        missionJob = viewModelScope.launch {
            try {
                when (step) {
                    0 -> {
                        missionStatus.value = "步骤1: 起飞中..."
                        takeoff(takeOffCallback)
                        delay(5000)
                        missionStatus.value = "起飞完成，点击下一步启用虚拟摇杆"
                    }
                    1 -> {
                        missionStatus.value = "步骤2: 启用虚拟摇杆..."
                        enableVirtualStick()
                        delay(1000)
                        missionStatus.value = "虚拟摇杆已启用，点击下一步爬升"
                    }
                    2 -> {
                        missionStatus.value = "步骤3: 爬升到 ${flightAltitude.value}m..."
                        climbToAltitude(flightAltitude.value ?: 10.0)
                        missionStatus.value = "爬升完成，点击下一步转向"
                    }
                    3 -> {
                        val start = startPoint.value!!
                        val end = endPoint.value!!
                        val bearing = calculateBearing(start, end)
                        missionStatus.value = "步骤4: 机头转向目标方向..."
                        rotateToHeading(bearing)
                        delay(500)
                        missionStatus.value = "转向完成，点击下一步飞往终点"
                    }
                    4 -> {
                        val start = startPoint.value!!
                        val end = endPoint.value!!
                        missionStatus.value = "步骤5: 飞往终点..."
                        flyToPoint(start, end)
                        missionStatus.value = "到达终点，点击下一步禁用虚拟摇杆"
                    }
                    5 -> {
                        missionStatus.value = "步骤6: 禁用虚拟摇杆..."
                        disableVirtualStick()
                        delay(1000)
                        missionStatus.value = "虚拟摇杆已禁用，点击下一步降落"
                    }
                    6 -> {
                        missionStatus.value = "步骤7: 降落中..."
                        land(landingCallback)
                        delay(5000)
                        missionStatus.value = "降落完成，点击下一步开始停留"
                    }
                    7 -> {
                        val duration = stayDuration.value ?: 5
                        missionStatus.value = "步骤8: 停留 ${duration} 秒..."
                        delay(duration * 1000L)
                        missionStatus.value = "停留完成，点击下一步起飞返回"
                    }
                    8 -> {
                        missionStatus.value = "步骤9: 起飞返回..."
                        takeoff(takeOffCallback)
                        delay(5000)
                        missionStatus.value = "起飞完成，点击下一步启用虚拟摇杆"
                    }
                    9 -> {
                        missionStatus.value = "步骤10: 启用虚拟摇杆..."
                        enableVirtualStick()
                        delay(1000)
                        missionStatus.value = "虚拟摇杆已启用，点击下一步爬升"
                    }
                    10 -> {
                        missionStatus.value = "步骤11: 爬升到 ${flightAltitude.value}m..."
                        climbToAltitude(flightAltitude.value ?: 10.0)
                        missionStatus.value = "爬升完成，点击下一步转向起点"
                    }
                    11 -> {
                        val start = startPoint.value!!
                        val end = endPoint.value!!
                        val bearing = calculateBearing(end, start)
                        missionStatus.value = "步骤12: 机头转向起点..."
                        rotateToHeading(bearing)
                        delay(500)
                        missionStatus.value = "转向完成，点击下一步返回起点"
                    }
                    12 -> {
                        val start = startPoint.value!!
                        val end = endPoint.value!!
                        missionStatus.value = "步骤13: 返回起点..."
                        flyToPoint(end, start)
                        missionStatus.value = "到达起点，点击下一步禁用虚拟摇杆"
                    }
                    13 -> {
                        missionStatus.value = "步骤14: 禁用虚拟摇杆..."
                        disableVirtualStick()
                        delay(1000)
                        missionStatus.value = "虚拟摇杆已禁用，点击下一步降落"
                    }
                    14 -> {
                        missionStatus.value = "步骤15: 降落中..."
                        land(landingCallback)
                        delay(5000)
                        missionStatus.value = "降落完成，任务结束"
                    }
                    15 -> {
                        disableVirtualStick()
                        missionStatus.value = "任务完成！"
                        isFlying.value = false
                        currentStep.value = 0
                        return@launch
                    }
                }
                currentStep.value = step + 1
                canProceed.value = true
            } catch (e: Exception) {
                missionStatus.value = "步骤失败: ${e.message}"
                canProceed.value = true
            }
        }
    }

    fun stopMission() {
        missionJob?.cancel()
        isFlying.value = false
        missionStatus.value = "任务已停止"

        // 先停止所有虚拟摇杆输入
        VirtualStickManager.getInstance().leftStick.verticalPosition = 0
        VirtualStickManager.getInstance().leftStick.horizontalPosition = 0
        VirtualStickManager.getInstance().rightStick.verticalPosition = 0
        VirtualStickManager.getInstance().rightStick.horizontalPosition = 0

        // 禁用虚拟摇杆，飞机会自动切换到GPS模式悬停
        // 这样你就可以用真实摇杆接管控制了
        disableVirtualStick()
    }

    private suspend fun enableVirtualStick() {
        suspendCancellableCoroutine<Unit> { continuation ->
            VirtualStickManager.getInstance().enableVirtualStick(object : CommonCallbacks.CompletionCallback {
                override fun onSuccess() {
                    continuation.resume(Unit)
                }
                override fun onFailure(error: IDJIError) {
                    missionStatus.postValue("启用虚拟摇杆失败: $error")
                    continuation.resume(Unit) // 失败也继续，避免任务卡死
                }
            })
        }
    }

    private fun disableVirtualStick() {
        VirtualStickManager.getInstance().disableVirtualStick(null)
    }

    private fun takeoff(callback: (CommonCallbacks.CompletionCallbackWithParam<EmptyMsg>) -> Unit) {
        callback(object : CommonCallbacks.CompletionCallbackWithParam<EmptyMsg> {
            override fun onSuccess(t: EmptyMsg?) {}
            override fun onFailure(error: IDJIError) {
                missionStatus.postValue("起飞失败: $error")
            }
        })
    }

    private suspend fun land(callback: (CommonCallbacks.CompletionCallbackWithParam<EmptyMsg>) -> Unit) {
        // 关闭降落保护，确保能降到地面
        missionStatus.postValue("准备降落：关闭降落保护...")

        KeyManager.getInstance().setValue(
            KeyTools.createKey(FlightAssistantKey.KeyLandingProtectionEnabled),
            false,
            object : CommonCallbacks.CompletionCallback {
                override fun onSuccess() {
                    missionStatus.postValue("降落保护已关闭")
                }
                override fun onFailure(error: IDJIError) {
                    missionStatus.postValue("关闭降落保护失败: $error")
                }
            }
        )

        delay(500)

        // 开始降落
        callback(object : CommonCallbacks.CompletionCallbackWithParam<EmptyMsg> {
            override fun onSuccess(t: EmptyMsg?) {}
            override fun onFailure(error: IDJIError) {
                missionStatus.postValue("降落命令失败: $error")
            }
        })

        var attempts = 0
        val maxAttempts = 120
        var motorOffCount = 0

        while (attempts < maxAttempts) {
            delay(1000)

            // 检查是否需要降落确认
            val needConfirm = suspendCancellableCoroutine<Boolean> { continuation ->
                KeyManager.getInstance().getValue(
                    KeyTools.createKey(FlightControllerKey.KeyIsLandingConfirmationNeeded),
                    object : CommonCallbacks.CompletionCallbackWithParam<Boolean> {
                        override fun onSuccess(v: Boolean?) { continuation.resume(v ?: false) }
                        override fun onFailure(error: IDJIError) { continuation.resume(false) }
                    }
                )
            }

            // 如果需要确认，调用确认降落
            if (needConfirm) {
                missionStatus.postValue("检测到降落确认请求，正在确认...")
                KeyManager.getInstance().performAction(
                    KeyTools.createKey(FlightControllerKey.KeyConfirmLanding),
                    object : CommonCallbacks.CompletionCallbackWithParam<EmptyMsg> {
                        override fun onSuccess(t: EmptyMsg?) {
                            missionStatus.postValue("降落确认成功，继续降落")
                        }
                        override fun onFailure(error: IDJIError) {
                            missionStatus.postValue("降落确认失败: $error")
                        }
                    }
                )
            }

            val areMotorsOn = suspendCancellableCoroutine<Boolean> { continuation ->
                KeyManager.getInstance().getValue(
                    KeyTools.createKey(FlightControllerKey.KeyAreMotorsOn),
                    object : CommonCallbacks.CompletionCallbackWithParam<Boolean> {
                        override fun onSuccess(v: Boolean?) { continuation.resume(v ?: true) }
                        override fun onFailure(error: IDJIError) { continuation.resume(true) }
                    }
                )
            }

            val altitude = suspendCancellableCoroutine<Double> { continuation ->
                KeyManager.getInstance().getValue(
                    KeyTools.createKey(FlightControllerKey.KeyAltitude),
                    object : CommonCallbacks.CompletionCallbackWithParam<Double> {
                        override fun onSuccess(v: Double?) { continuation.resume(v ?: 0.0) }
                        override fun onFailure(error: IDJIError) { continuation.resume(0.0) }
                    }
                )
            }

            missionStatus.postValue("降落中... 高度: %.1f 米, 电机: ${if (areMotorsOn) "运行" else "停止"} (${attempts + 1}s)".format(altitude))

            if (!areMotorsOn) {
                motorOffCount++
                if (motorOffCount >= 2) {
                    missionStatus.postValue("电机停止，降落完成")
                    break
                }
            } else {
                motorOffCount = 0
            }

            attempts++
        }

        if (attempts >= maxAttempts) {
            missionStatus.postValue("降落超时(120s)，强制确认完成")
        }
    }
    private suspend fun climbToAltitude(targetAltitude: Double) {
        val speed = flightSpeed.value ?: 2.0
        val speedRatio = (speed / 15.0).coerceIn(0.1, 0.5)
        val verticalStickValue = (speedRatio * Stick.MAX_STICK_POSITION_ABS).toInt()

        missionStatus.postValue("目标高度: %.1f 米".format(targetAltitude))

        while (true) {
            val currentAltitude = suspendCancellableCoroutine<Double> { continuation ->
                KeyManager.getInstance().getValue(
                    KeyTools.createKey(FlightControllerKey.KeyAltitude),
                    object : CommonCallbacks.CompletionCallbackWithParam<Double> {
                        override fun onSuccess(altitude: Double?) { continuation.resume(altitude ?: 0.0) }
                        override fun onFailure(error: IDJIError) { continuation.resume(0.0) }
                    }
                )
            }

            val heightDiff = targetAltitude - currentAltitude
            missionStatus.postValue("当前高度: %.1f 米, 目标: %.1f 米, 差: %.1f 米".format(currentAltitude, targetAltitude, heightDiff))

            if (abs(heightDiff) < 0.5) {
                VirtualStickManager.getInstance().leftStick.verticalPosition = 0
                missionStatus.postValue("到达目标高度！")
                break
            }

            val adjustedStickValue = if (heightDiff > 0) verticalStickValue else -verticalStickValue
            VirtualStickManager.getInstance().leftStick.verticalPosition = adjustedStickValue
            delay(200)
        }

        VirtualStickManager.getInstance().leftStick.verticalPosition = 0
    }

    private suspend fun flyToPoint(from: GPSPoint, to: GPSPoint) {
        val targetDistance = calculateDistance(from, to)
        val speed = flightSpeed.value ?: 2.0
        val speedRatio = speed / 15.0
        val stickValue = (speedRatio * Stick.MAX_STICK_POSITION_ABS).toInt()

        missionStatus.postValue("目标距离: %.1f 米, 飞行速度: %.1f m/s".format(targetDistance, speed))

        var attempts = 0
        val maxAttempts = 300

        while (attempts < maxAttempts) {
            val currentPos = suspendCancellableCoroutine<LocationCoordinate2D?> { continuation ->
                KeyManager.getInstance().getValue(
                    KeyTools.createKey(FlightControllerKey.KeyAircraftLocation),
                    object : CommonCallbacks.CompletionCallbackWithParam<LocationCoordinate2D> {
                        override fun onSuccess(location: LocationCoordinate2D?) { continuation.resume(location) }
                        override fun onFailure(error: IDJIError) { continuation.resume(null) }
                    }
                )
            }

            if (currentPos == null) {
                delay(200)
                attempts++
                continue
            }

            val currentPoint = GPSPoint(currentPos.latitude, currentPos.longitude, 0.0)
            val remainingDistance = calculateDistance(currentPoint, to)
            missionStatus.postValue("剩余距离: %.1f 米".format(remainingDistance))

            if (remainingDistance < 2.0) {
                VirtualStickManager.getInstance().rightStick.verticalPosition = 0
                VirtualStickManager.getInstance().rightStick.horizontalPosition = 0
                missionStatus.postValue("到达目标点！")
                break
            }

            val currentBearing = calculateBearing(currentPoint, to)
            val currentHeading = suspendCancellableCoroutine<Double> { continuation ->
                KeyManager.getInstance().getValue(
                    KeyTools.createKey(FlightControllerKey.KeyCompassHeading),
                    object : CommonCallbacks.CompletionCallbackWithParam<Double> {
                        override fun onSuccess(heading: Double?) { continuation.resume(heading ?: 0.0) }
                        override fun onFailure(error: IDJIError) { continuation.resume(0.0) }
                    }
                )
            }

            var headingError = currentBearing - currentHeading
            if (headingError > 180) headingError -= 360
            if (headingError < -180) headingError += 360

            val yawValue = if (abs(headingError) > 5) {
                val yawRatio = (abs(headingError) / 180.0).coerceIn(0.1, 0.3)
                if (headingError > 0) (Stick.MAX_STICK_POSITION_ABS * yawRatio).toInt()
                else (-Stick.MAX_STICK_POSITION_ABS * yawRatio).toInt()
            } else 0

            VirtualStickManager.getInstance().rightStick.verticalPosition = stickValue
            VirtualStickManager.getInstance().leftStick.horizontalPosition = yawValue

            delay(200)
            attempts++
        }

        VirtualStickManager.getInstance().rightStick.verticalPosition = 0
        VirtualStickManager.getInstance().rightStick.horizontalPosition = 0
        VirtualStickManager.getInstance().leftStick.horizontalPosition = 0

        if (attempts >= maxAttempts) {
            missionStatus.postValue("飞行超时，已停止")
        }
    }

    private suspend fun rotateToHeading(targetHeading: Double) {
        missionStatus.postValue("开始转向，目标航向: ${targetHeading.toInt()}°")

        var attempts = 0
        val maxAttempts = 100

        while (attempts < maxAttempts) {
            val currentHeading = suspendCancellableCoroutine<Double> { continuation ->
                KeyManager.getInstance().getValue(
                    KeyTools.createKey(FlightControllerKey.KeyCompassHeading),
                    object : CommonCallbacks.CompletionCallbackWithParam<Double> {
                        override fun onSuccess(heading: Double?) { continuation.resume(heading ?: 0.0) }
                        override fun onFailure(error: IDJIError) { continuation.resume(0.0) }
                    }
                )
            }

            var angleDiff = targetHeading - currentHeading
            if (angleDiff > 180) angleDiff -= 360
            if (angleDiff < -180) angleDiff += 360

            missionStatus.postValue("当前: ${currentHeading.toInt()}°, 目标: ${targetHeading.toInt()}°, 差: ${angleDiff.toInt()}°")

            if (abs(angleDiff) < 3) {
                VirtualStickManager.getInstance().leftStick.horizontalPosition = 0
                missionStatus.postValue("转向完成！")
                break
            }

            val yawRatio = (abs(angleDiff) / 180.0).coerceIn(0.1, 0.5)
            val yawValue = if (angleDiff > 0)
                (Stick.MAX_STICK_POSITION_ABS * yawRatio).toInt()
            else
                (-Stick.MAX_STICK_POSITION_ABS * yawRatio).toInt()

            VirtualStickManager.getInstance().leftStick.horizontalPosition = yawValue
            delay(200)
            attempts++
        }

        VirtualStickManager.getInstance().leftStick.horizontalPosition = 0

        if (attempts >= maxAttempts) {
            missionStatus.postValue("转向超时，已停止")
        }
    }

    private fun calculateDistance(from: GPSPoint, to: GPSPoint): Double {
        val earthRadius = 6371000.0 // meters
        val dLat = Math.toRadians(to.latitude - from.latitude)
        val dLon = Math.toRadians(to.longitude - from.longitude)
        val a = sin(dLat / 2) * sin(dLat / 2) +
                cos(Math.toRadians(from.latitude)) * cos(Math.toRadians(to.latitude)) *
                sin(dLon / 2) * sin(dLon / 2)
        val c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return earthRadius * c
    }

    private fun calculateBearing(from: GPSPoint, to: GPSPoint): Double {
        val dLon = Math.toRadians(to.longitude - from.longitude)
        val lat1 = Math.toRadians(from.latitude)
        val lat2 = Math.toRadians(to.latitude)
        val y = sin(dLon) * cos(lat2)
        val x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dLon)
        var bearing = Math.toDegrees(atan2(y, x))
        // 转换到 0-360 度范围，与飞机指南针一致
        bearing = (bearing + 360) % 360
        return bearing
    }

    override fun onCleared() {
        super.onCleared()
        stopMission()
    }
}
