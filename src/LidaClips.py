from lidaclips.runtime import build_runtime


runtime = build_runtime()
app = runtime.app
socketio = runtime.socketio


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
