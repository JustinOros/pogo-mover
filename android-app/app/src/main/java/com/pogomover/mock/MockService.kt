package com.pogomover.mock

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.location.Location
import android.location.LocationManager
import android.location.provider.ProviderProperties
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import com.google.android.gms.location.LocationServices
import java.net.InetAddress
import java.net.ServerSocket
import kotlin.concurrent.thread

class MockService : Service() {

    @Volatile private var lat = 0.0
    @Volatile private var lng = 0.0
    @Volatile private var hasFix = false
    @Volatile private var running = true

    private lateinit var locationManager: LocationManager
    private val fused by lazy { LocationServices.getFusedLocationProviderClient(this) }
    private val handler = Handler(Looper.getMainLooper())
    private var server: ServerSocket? = null
    private val providers = listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        startAsForeground()
        locationManager = getSystemService(LOCATION_SERVICE) as LocationManager
        setupProviders()
        try {
            fused.setMockMode(true)
        } catch (e: SecurityException) {
        }
        startServer()
        handler.post(tick)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onDestroy() {
        running = false
        handler.removeCallbacksAndMessages(null)
        try {
            server?.close()
        } catch (e: Exception) {
        }
        try {
            fused.setMockMode(false)
        } catch (e: Exception) {
        }
        for (p in providers) {
            try {
                locationManager.removeTestProvider(p)
            } catch (e: Exception) {
            }
        }
        super.onDestroy()
    }

    private fun startAsForeground() {
        val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(
            NotificationChannel("mock", "Mock location", NotificationManager.IMPORTANCE_LOW)
        )
        val notification = Notification.Builder(this, "mock")
            .setContentTitle("PoGo Mock is running")
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setOngoing(true)
            .build()
        startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION)
    }

    private fun setupProviders() {
        for (p in providers) {
            try {
                locationManager.removeTestProvider(p)
            } catch (e: Exception) {
            }
            try {
                locationManager.addTestProvider(
                    p, false, false, false, false, true, true, true,
                    ProviderProperties.POWER_USAGE_LOW, ProviderProperties.ACCURACY_FINE
                )
                locationManager.setTestProviderEnabled(p, true)
            } catch (e: Exception) {
            }
        }
    }

    private fun build(provider: String): Location {
        return Location(provider).apply {
            latitude = lat
            longitude = lng
            altitude = 0.0
            accuracy = 5f
            time = System.currentTimeMillis()
            elapsedRealtimeNanos = SystemClock.elapsedRealtimeNanos()
        }
    }

    private val tick = object : Runnable {
        override fun run() {
            if (!running) return
            if (hasFix) {
                for (p in providers) {
                    try {
                        locationManager.setTestProviderLocation(p, build(p))
                    } catch (e: Exception) {
                    }
                }
                try {
                    fused.setMockLocation(build("fused"))
                } catch (e: Exception) {
                }
            }
            handler.postDelayed(this, 500)
        }
    }

    private fun startServer() {
        thread(isDaemon = true) {
            try {
                server = ServerSocket(7788, 4, InetAddress.getByName("127.0.0.1"))
                while (running) {
                    val client = server!!.accept()
                    thread(isDaemon = true) { handleClient(client) }
                }
            } catch (e: Exception) {
            }
        }
    }

    private fun handleClient(client: java.net.Socket) {
        try {
            client.use { socket ->
                val reader = socket.getInputStream().bufferedReader()
                val writer = socket.getOutputStream()
                while (running) {
                    val line = reader.readLine() ?: break
                    if (line.trim() == "ping") {
                        writer.write("ok\n".toByteArray())
                        writer.flush()
                        continue
                    }
                    val parts = line.split(",")
                    if (parts.size == 2) {
                        val a = parts[0].trim().toDoubleOrNull()
                        val b = parts[1].trim().toDoubleOrNull()
                        if (a != null && b != null) {
                            lat = a
                            lng = b
                            hasFix = true
                        }
                    }
                }
            }
        } catch (e: Exception) {
        }
    }
}
