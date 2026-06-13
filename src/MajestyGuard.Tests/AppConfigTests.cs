using MajestyGuard.Core.Models;

namespace MajestyGuard.Tests;

public class AppConfigTests
{
    private const string EnrolledUserSid = "S-1-5-21-1111111111-2222222222-3333333333-1002";
    private const string OtherUserSid = "S-1-5-21-1111111111-2222222222-3333333333-1003";
    private const string LocalSystemSid = "S-1-5-18";

    [Fact]
    public void IsEnrolledProfileForSid_ReturnsFalse_WhenNoUserIsEnrolled()
    {
        var config = new AppConfig();

        Assert.False(config.IsEnrolledProfileForSid(EnrolledUserSid));
    }

    [Fact]
    public void IsEnrolledProfileForSid_AllowsMatchingEnrolledUser()
    {
        var config = new AppConfig { EnrolledUserSid = EnrolledUserSid };

        Assert.True(config.IsEnrolledProfileForSid(EnrolledUserSid.ToLowerInvariant()));
    }

    [Fact]
    public void IsEnrolledProfileForSid_RejectsDifferentInteractiveUser()
    {
        var config = new AppConfig { EnrolledUserSid = EnrolledUserSid };

        Assert.False(config.IsEnrolledProfileForSid(OtherUserSid));
    }

    [Fact]
    public void IsEnrolledProfileForSid_AllowsLocalSystemServiceForConfiguredEnrollment()
    {
        var config = new AppConfig { EnrolledUserSid = EnrolledUserSid };

        Assert.True(config.IsEnrolledProfileForSid(LocalSystemSid));
    }
}
