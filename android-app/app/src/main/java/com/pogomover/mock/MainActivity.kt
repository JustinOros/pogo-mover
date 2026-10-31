package com.pogomover.mock

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

class MainActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(48, 48, 48, 48)
        }

        val infoView = TextView(this).apply {
            text = "PoGo Mock\n\n1. Developer options > Select mock location app > PoGo Mock\n2. Tap Start\n3. Run start.py on the Mac"
            textSize = 16f
        }

        val startButton = Button(this).apply {
            text = "Start"
            setOnClickListener {
                startForegroundService(Intent(this@MainActivity, MockService::class.java))
            }
        }

        val stopButton = Button(this).apply {
            text = "Stop"
            setOnClickListener {
                stopService(Intent(this@MainActivity, MockService::class.java))
            }
        }

        requestPermissions(
            arrayOf(
                Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.POST_NOTIFICATIONS
            ),
            1
        )

        if (intent.getBooleanExtra("autostart", false)) {
            startForegroundService(Intent(this, MockService::class.java))
        }

        layout.addView(infoView)
        layout.addView(startButton)
        layout.addView(stopButton)
        setContentView(layout)
    }
}
