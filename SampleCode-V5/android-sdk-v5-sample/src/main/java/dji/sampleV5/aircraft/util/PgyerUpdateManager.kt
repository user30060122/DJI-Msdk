package dji.sampleV5.aircraft.util

import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * 蒲公英版本更新管理器
 */
object PgyerUpdateManager {

    private const val API_KEY = "0eb7dd4699386c709d907f21f9579832"
    private const val APP_KEY = "9ec5f6aea496c15c355a1ce2d3559447"
    private const val CHECK_URL = "https://www.pgyer.com/apiv2/app/check"

    /**
     * 检查更新
     */
    suspend fun checkUpdate(context: Context) {
        try {
            val updateInfo = withContext(Dispatchers.IO) {
                fetchUpdateInfo(context)
            }

            if (updateInfo != null) {
                withContext(Dispatchers.Main) {
                    showUpdateDialog(context, updateInfo)
                }
            }
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    private fun fetchUpdateInfo(context: Context): UpdateInfo? {
        val currentVersion = getCurrentVersionCode(context)
        val url = URL("$CHECK_URL?_api_key=$API_KEY&appKey=$APP_KEY&buildVersion=$currentVersion")

        val connection = url.openConnection() as HttpURLConnection
        connection.requestMethod = "POST"
        connection.connectTimeout = 10000
        connection.readTimeout = 10000

        return try {
            val responseCode = connection.responseCode
            if (responseCode == HttpURLConnection.HTTP_OK) {
                val response = connection.inputStream.bufferedReader().readText()
                parseUpdateInfo(response)
            } else {
                null
            }
        } finally {
            connection.disconnect()
        }
    }

    private fun parseUpdateInfo(json: String): UpdateInfo? {
        val jsonObj = JSONObject(json)
        val code = jsonObj.optInt("code")

        if (code == 0) {
            val data = jsonObj.getJSONObject("data")
            val buildHaveNewVersion = data.optBoolean("buildHaveNewVersion", false)

            if (buildHaveNewVersion) {
                return UpdateInfo(
                    version = data.optString("buildVersion", ""),
                    versionName = data.optString("buildVersionNo", ""),
                    updateLog = data.optString("buildUpdateDescription", ""),
                    downloadUrl = data.optString("downloadURL", "")
                )
            }
        }
        return null
    }

    private fun getCurrentVersionCode(context: Context): String {
        return try {
            val packageInfo = context.packageManager.getPackageInfo(context.packageName, 0)
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.P) {
                packageInfo.longVersionCode.toString()
            } else {
                @Suppress("DEPRECATION")
                packageInfo.versionCode.toString()
            }
        } catch (e: Exception) {
            "0"
        }
    }

    private fun showUpdateDialog(context: Context, info: UpdateInfo) {
        AlertDialog.Builder(context)
            .setTitle("发现新版本 ${info.versionName}")
            .setMessage(info.updateLog)
            .setPositiveButton("立即更新") { _, _ ->
                openDownloadUrl(context, info.downloadUrl)
            }
            .setNegativeButton("稍后提醒", null)
            .setCancelable(true)
            .show()
    }

    private fun openDownloadUrl(context: Context, url: String) {
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
        context.startActivity(intent)
    }

    data class UpdateInfo(
        val version: String,
        val versionName: String,
        val updateLog: String,
        val downloadUrl: String
    )
}
