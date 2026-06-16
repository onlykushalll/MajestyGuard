// MainPage.xaml.cs — MajestyGuard Companion Device registration UI.

using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using MajestyGuard.Companion.Services;
using System;
using System.Threading.Tasks;

namespace MajestyGuard.Companion
{
    public sealed partial class MainPage : Page
    {
        private readonly CompanionRegistration _registration = new();
        private bool _isRegistered = false;

        public MainPage()
        {
            this.InitializeComponent();
            _ = RefreshStatusAsync();
        }

        private async Task RefreshStatusAsync()
        {
            try
            {
                _isRegistered = await _registration.IsRegisteredAsync();
                UpdateUI(_isRegistered);
            }
            catch (Exception ex)
            {
                ShowMessage(InfoBarSeverity.Warning, "Could not check registration status", ex.Message);
            }
        }

        private void UpdateUI(bool registered)
        {
            if (registered)
            {
                StatusIcon.Glyph      = "\uF78B";
                StatusIcon.Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.ForestGreen);
                StatusTitle.Text      = "Registered";
                StatusDetail.Text     = "This device is paired with Windows Hello. Face unlock is active when the daemon is running.";
                RegisterButton.IsEnabled   = false;
                UnregisterButton.IsEnabled = true;
            }
            else
            {
                StatusIcon.Glyph      = "\uEA3A";
                StatusIcon.Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.Gray);
                StatusTitle.Text      = "Not Registered";
                StatusDetail.Text     = "Register this device to enable face-unlock via Windows Hello.";
                RegisterButton.IsEnabled   = true;
                UnregisterButton.IsEnabled = false;
            }
        }

        private async void OnRegisterClick(object sender, RoutedEventArgs e)
        {
            SetBusy(true);
            HideMessage();
            try
            {
                var result = await _registration.RegisterAsync();
                if (result.Success)
                {
                    _isRegistered = true;
                    UpdateUI(true);
                    ShowMessage(InfoBarSeverity.Success, "Device registered",
                        "Face unlock is now enabled. Start the MajestyGuard daemon to activate.");
                }
                else
                {
                    ShowMessage(InfoBarSeverity.Error, "Registration failed",
                        result.ErrorMessage ?? "Unknown error. Check that setup_whcdf.ps1 was run as Administrator.");
                }
            }
            catch (Exception ex)
            {
                ShowMessage(InfoBarSeverity.Error, "Registration error", ex.Message);
            }
            finally { SetBusy(false); }
        }

        private async void OnUnregisterClick(object sender, RoutedEventArgs e)
        {
            var dialog = new ContentDialog
            {
                Title             = "Unregister device?",
                Content           = "This will remove face unlock. You will need to re-register to use it again.",
                PrimaryButtonText = "Unregister",
                CloseButtonText   = "Cancel",
                DefaultButton     = ContentDialogButton.Close,
                XamlRoot          = this.XamlRoot,
            };
            if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;

            SetBusy(true);
            HideMessage();
            try
            {
                await _registration.UnregisterAsync();
                _isRegistered = false;
                UpdateUI(false);
                ShowMessage(InfoBarSeverity.Informational, "Device unregistered", "Face unlock has been disabled.");
            }
            catch (Exception ex)
            {
                ShowMessage(InfoBarSeverity.Error, "Unregister error", ex.Message);
            }
            finally { SetBusy(false); }
        }

        private void SetBusy(bool busy)
        {
            OpProgress.IsActive        = busy;
            OpProgress.Visibility      = busy ? Visibility.Visible : Visibility.Collapsed;
            RegisterButton.IsEnabled   = !busy && !_isRegistered;
            UnregisterButton.IsEnabled = !busy && _isRegistered;
        }

        private void ShowMessage(InfoBarSeverity severity, string title, string message)
        {
            MessageBar.Severity = severity;
            MessageBar.Title    = title;
            MessageBar.Message  = message;
            MessageBar.IsOpen   = true;
        }

        private void HideMessage() => MessageBar.IsOpen = false;
    }
}
