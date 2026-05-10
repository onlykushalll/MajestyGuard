// MajestyGuard.Service/Program.cs
// Host setup for the Windows Service.
// All dependencies registered here. Worker.cs is the entry point.

using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using MajestyGuard.Core;
using MajestyGuard.Core.Models;
using MajestyGuard.Service;

var host = Host.CreateDefaultBuilder(args)
    .UseWindowsService(opts =>
    {
        opts.ServiceName = "MajestyGuardService";
    })
    .ConfigureLogging(logging =>
    {
        logging.ClearProviders();
        logging.AddEventLog(settings =>
        {
            settings.SourceName = "MajestyGuard";
        });
        // Also log to file in debug builds
#if DEBUG
        logging.AddConsole();
        logging.SetMinimumLevel(LogLevel.Debug);
#else
        logging.SetMinimumLevel(LogLevel.Information);
#endif
    })
    .ConfigureServices((ctx, services) =>
    {
        // ── Configuration ────────────────────────────────────────────
        var config = AppConfig.Load();
        services.AddSingleton(config);

        // ── Core ─────────────────────────────────────────────────────
        services.AddSingleton<StateMachine>();

        // ── Service components ────────────────────────────────────────
        services.AddSingleton<ProcessRestrictor>();
        services.AddSingleton<SocialLockEngine>();
        services.AddSingleton<PresenceMonitor>();
        services.AddSingleton<InactivityWatcher>();
        services.AddSingleton<DesktopWatchdog>();

        // ── Worker (main service) ─────────────────────────────────────
        services.AddHostedService<Worker>();
    })
    .Build();

await host.RunAsync();
