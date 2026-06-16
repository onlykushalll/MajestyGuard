using Microsoft.UI.Xaml;
using System;
using System.Diagnostics;

namespace MajestyGuard.Companion
{
    public partial class App : Application
    {
        private Window? _mainWindow;

        public App()
        {
            this.InitializeComponent();
        }

        protected override void OnLaunched(LaunchActivatedEventArgs args)
        {
            _mainWindow         = new Window();
            _mainWindow.Content = new MainPage();
            _mainWindow.Title   = "MajestyGuard Companion";
            _mainWindow.Activate();

            // Register background task — fire-and-forget, never crash the app.
            // If WHCDF is not provisioned (DisabledByPolicy), the UI still shows
            // and the user sees the error when they click Register.
            _ = TryRegisterBackgroundTaskAsync();
        }

        private static async System.Threading.Tasks.Task TryRegisterBackgroundTaskAsync()
        {
            try
            {
                await Background.UnlockTask.EnsureRegisteredAsync();
            }
            catch (Exception ex)
            {
                // Non-fatal — WHCDF may not be provisioned yet.
                // The MainPage registration flow handles this gracefully.
                Debug.WriteLine($"[App] Background task registration skipped: {ex.Message}");
            }
        }
    }
}
