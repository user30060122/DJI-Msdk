package dji.sampleV5.aircraft.util

import android.content.Context
import android.provider.Settings
import android.util.Log
import org.eclipse.paho.client.mqttv3.*
import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence
import org.json.JSONObject
import java.util.concurrent.Executors

/**
 * MQTT管理器 - 负责与电脑端通信
 * 话题设计:
 *   dji/drone/{droneId}/command  <- 电脑发送任务指令
 *   dji/drone/{droneId}/status   -> 手机上报飞机状态
 */
object MqttManager {

    private const val TAG = "MqttManager"
    private const val BROKER = "tcp://broker.emqx.io:1883"

    lateinit var droneId: String
        private set

    private var client: MqttClient? = null
    private val executor = Executors.newSingleThreadExecutor()

    var onCommandReceived: ((action: String, data: JSONObject) -> Unit)? = null
    var onConnectionChanged: ((connected: Boolean) -> Unit)? = null

    fun init(context: Context) {
        droneId = Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID)
            ?: "drone_unknown"
        connect()
    }

    private fun connect() {
        executor.execute {
            try {
                val clientId = "drone_$droneId"
                client = MqttClient(BROKER, clientId, MemoryPersistence())

                val options = MqttConnectOptions().apply {
                    isCleanSession = true
                    connectionTimeout = 10
                    keepAliveInterval = 30
                    isAutomaticReconnect = true
                }

                client?.setCallback(object : MqttCallback {
                    override fun connectionLost(cause: Throwable?) {
                        Log.w(TAG, "MQTT连接断开: ${cause?.message}")
                        onConnectionChanged?.invoke(false)
                        // 自动重连
                        executor.execute {
                            Thread.sleep(2000)
                            reconnect()
                        }
                    }

                    override fun messageArrived(topic: String, message: MqttMessage) {
                        handleMessage(topic, message.toString())
                    }

                    override fun deliveryComplete(token: IMqttDeliveryToken?) {}
                })

                client?.connect(options)
                client?.subscribe("dji/drone/$droneId/command", 1)
                Log.i(TAG, "MQTT连接成功, droneId=$droneId")
                onConnectionChanged?.invoke(true)

            } catch (e: Exception) {
                Log.e(TAG, "MQTT连接失败: ${e.message}")
                onConnectionChanged?.invoke(false)
            }
        }
    }

    private fun handleMessage(topic: String, payload: String) {
        try {
            val json = JSONObject(payload)
            val action = json.optString("action", "")
            Log.i(TAG, "收到指令: action=$action")
            onCommandReceived?.invoke(action, json)
        } catch (e: Exception) {
            Log.e(TAG, "解析指令失败: $payload")
        }
    }

    fun isConnected(): Boolean {
        return client?.isConnected == true
    }

    fun reconnect() {
        if (isConnected()) {
            Log.i(TAG, "MQTT已连接，无需重连")
            return
        }
        Log.i(TAG, "尝试重连MQTT...")
        try {
            client?.disconnect()
        } catch (e: Exception) {
            // 忽略断开错误
        }
        connect()
    }

    fun publishStatus(
        lat: Double, lng: Double, altitude: Double,
        missionStatus: String, isFlying: Boolean
    ) {
        executor.execute {
            try {
                val json = JSONObject().apply {
                    put("drone_id", droneId)
                    put("lat", lat)
                    put("lng", lng)
                    put("altitude", altitude)
                    put("mission_status", missionStatus)
                    put("is_flying", isFlying)
                    put("timestamp", System.currentTimeMillis())
                }
                val topic = "dji/drone/$droneId/status"
                client?.publish(topic, MqttMessage(json.toString().toByteArray()).apply { qos = 0 })
            } catch (e: Exception) {
                Log.e(TAG, "上报状态失败: ${e.message}")
            }
        }
    }

    fun disconnect() {
        executor.execute {
            try {
                client?.disconnect()
            } catch (e: Exception) {
                Log.e(TAG, "断开失败: ${e.message}")
            }
        }
    }
}
